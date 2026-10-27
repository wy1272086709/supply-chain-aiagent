"""文档服务 — 对应 src/document/document.service.ts。

元数据 PG（kh_document）、正文 Mongo（document_content），content_id ↔ Mongo _id。
"""

from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.core.snowflake import next_snowflake_id
from app.db import mongo
from app.db.models import Document
from app.modules.documents import parsers
from app.modules.documents.access import access_from_user, can_read_document, can_write_document
from app.modules.documents.parsers import file_parser
from app.modules.documents.parsers.markdown_util import (
    decode_upload_filename,
    get_extension,
    title_from_filename,
)
from app.modules.documents.status import (
    DocumentStatus,
    can_archive,
    can_edit_content,
    can_publish_from,
)
from app.modules.storage import rustfs
from app.modules.users import serializers
from app.common.now import utcnow

logger = logging.getLogger(__name__)


def is_require_approval() -> bool:
    return get_settings().approval_required


def count_words(content: str) -> int:
    """中英混合字数：CJK 每字 1，拉丁按空白分词。"""
    trimmed = (content or "").strip()
    if not trimmed:
        return 0
    cjk = len(re.findall(r"[一-鿿]", trimmed))
    latin_part = re.sub(r"[一-鿿]", " ", trimmed).strip()
    latin = len([w for w in re.split(r"\s+", latin_part) if w]) if latin_part else 0
    return cjk + latin


def build_content_summary(content: str, max_len: int = 200) -> str:
    trimmed = re.sub(r"\s+", " ", (content or "").strip())
    return trimmed if len(trimmed) <= max_len else trimmed[:max_len] + "..."


async def create(db: AsyncSession, dto: dict, actor: dict) -> dict:
    requested_status = dto.get("status") if dto.get("status") is not None else DocumentStatus.DRAFT
    if requested_status not in (DocumentStatus.DRAFT, DocumentStatus.PUBLISHED):
        raise BadRequestError("创建文档仅允许草稿或已发布状态")
    if requested_status == DocumentStatus.PUBLISHED and is_require_approval():
        raise BadRequestError("开启审核时请先创建草稿，再提交发布/审核")

    doc_id = next_snowflake_id()
    content = dto.get("content") or ""
    word_count = count_words(content)
    content_summary = dto.get("summary") or build_content_summary(content)

    # 先写 Mongo，失败补偿由 except 分支处理
    content_doc = await mongo.create_content(doc_id, content, content_summary)
    content_id = str(content_doc["_id"])

    try:
        now = utcnow()
        doc = Document(
            id=doc_id,
            title=dto["title"],
            content_id=content_id,
            summary=dto.get("summary"),
            category_id=dto.get("categoryId"),
            team_id=dto.get("teamId"),
            author_id=actor["userId"],
            cover_image=dto.get("coverImage"),
            tags=dto.get("tags"),
            status=requested_status,
            remark=dto.get("remark"),
            is_public=dto.get("isPublic") or False,
            word_count=word_count,
            publish_time=now if requested_status == DocumentStatus.PUBLISHED else None,
            create_by=actor["userId"],
            update_by=actor["userId"],
            deleted=False,
            created_at=now,
            updated_at=now,
        )
        db.add(doc)
        await db.commit()

        if requested_status == DocumentStatus.PUBLISHED:
            await _safe_publish(doc)
        return {**serializers.document_vo(doc), "content": content}
    except Exception:
        await db.rollback()
        await mongo.hard_delete_content(content_id)
        raise


async def find_all(db: AsyncSession, query: dict, user: dict) -> dict:
    page = query.get("page") or 1
    page_size = query.get("pageSize") or 20
    scope = access_from_user(user)

    base = select(Document).where(Document.deleted.is_(False))
    count_q = select(func.count()).select_from(Document).where(Document.deleted.is_(False))

    if not scope["unrestricted"]:
        if scope["teamIds"]:
            cond = (
                Document.author_id == scope["userId"]
            ) | (
                (Document.status == DocumentStatus.PUBLISHED)
                & (Document.is_public.is_(True) | Document.team_id.in_(scope["teamIds"]))
            )
        else:
            cond = (Document.author_id == scope["userId"]) | (
                (Document.status == DocumentStatus.PUBLISHED) & Document.is_public.is_(True)
            )
        base = base.where(cond)
        count_q = count_q.where(cond)

    if query.get("title"):
        cond = Document.title.ilike(f"%{query['title']}%")
        base = base.where(cond)
        count_q = count_q.where(cond)
    for field, column in [
        ("categoryId", Document.category_id),
        ("teamId", Document.team_id),
        ("authorId", Document.author_id),
        ("status", Document.status),
    ]:
        if query.get(field) is not None:
            cond = column == query[field]
            base = base.where(cond)
            count_q = count_q.where(cond)

    total = (await db.execute(count_q)).scalar_one()
    docs = (
        (
            await db.execute(
                base.order_by(Document.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [serializers.document_vo(d) for d in docs],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def find_one(db: AsyncSession, doc_id: str, with_content: bool = True, user: dict | None = None) -> dict:
    doc = await _find_or_throw(db, doc_id)
    if user and not can_read_document(doc, access_from_user(user)):
        raise ForbiddenError("无权查看该文档")
    if not with_content:
        return serializers.document_vo(doc)
    content_doc = await mongo.find_content(doc.content_id)
    return {**serializers.document_vo(doc), "content": (content_doc or {}).get("content", "")}


async def update(db: AsyncSession, doc_id: str, dto: dict, actor: dict) -> dict:
    doc = await _find_or_throw(db, doc_id)
    _assert_writable(doc, actor)

    old_status = doc.status
    old_is_public = doc.is_public
    old_team_id = doc.team_id

    if doc.status == DocumentStatus.PENDING_REVIEW:
        if dto.get("content") is not None or dto.get("title") is not None:
            raise BadRequestError("审核中的文档不可编辑")
    elif not can_edit_content(doc.status):
        raise BadRequestError("当前文档状态不允许编辑")

    new_status = dto.get("status")
    if new_status is not None and new_status != doc.status:
        if new_status == DocumentStatus.DRAFT and doc.status == DocumentStatus.PUBLISHED:
            doc.status = DocumentStatus.DRAFT
        else:
            raise BadRequestError("请使用 publish / archive / save-draft / 审核接口变更文档状态")

    content_changed = False
    new_content: str | None = None

    if dto.get("content") is not None:
        content_changed = True
        new_content = dto["content"]
        content_summary = dto.get("summary") or build_content_summary(dto["content"])
        updated = await mongo.update_content(doc.content_id, dto["content"], content_summary)
        if not updated:
            raise BadRequestError(f"Document content {doc.content_id} not found")
        doc.word_count = count_words(dto["content"])
    elif dto.get("summary") is not None:
        # 只改摘要：同步 Mongo 侧 contentSummary（$set，不递增版本）
        await _set_content_summary(doc.content_id, dto["summary"])

    field_map = {
        "title": "title",
        "summary": "summary",
        "categoryId": "category_id",
        "teamId": "team_id",
        "coverImage": "cover_image",
        "tags": "tags",
        "remark": "remark",
        "isPublic": "is_public",
    }
    for field, column in field_map.items():
        if dto.get(field) is not None:
            setattr(doc, column, dto[field])
    doc.update_by = actor["userId"]
    doc.updated_at = utcnow()

    await db.commit()
    final_content = new_content if new_content is not None else await _load_content(doc.content_id)

    visibility_changed = doc.is_public != old_is_public or (doc.team_id or None) != old_team_id
    await _sync_pipeline_after_update(doc, old_status, doc.status, content_changed, visibility_changed)

    return {**serializers.document_vo(doc), "content": final_content}


async def publish(db: AsyncSession, doc_id: str, actor: dict) -> dict:
    logger.info("发布文档：documentId=%s", doc_id)
    doc = await _find_or_throw(db, doc_id)
    _assert_writable(doc, actor)

    if not can_publish_from(doc.status):
        raise BadRequestError("当前文档状态不允许发布")
    if doc.status == DocumentStatus.PENDING_REVIEW:
        raise BadRequestError("文档审核中，请等待审核结果")

    if is_require_approval():
        if doc.status in (DocumentStatus.DRAFT, DocumentStatus.PUBLISHED):
            from app.modules.documents import review_service

            saved = await review_service.submit_for_review(db, doc_id, actor)
            content = await _load_content(saved.content_id)
            return {**serializers.document_vo(saved), "content": content}

    return await direct_publish(db, doc_id, actor)


async def direct_publish(db: AsyncSession, doc_id: str, actor: dict | None = None) -> dict:
    doc = await _find_or_throw(db, doc_id)
    if doc.status not in (
        DocumentStatus.DRAFT,
        DocumentStatus.PUBLISHED,
        DocumentStatus.ARCHIVED,
        DocumentStatus.PENDING_REVIEW,
    ):
        raise BadRequestError("当前文档状态不允许发布")

    doc.status = DocumentStatus.PUBLISHED
    doc.publish_time = utcnow()
    if actor and actor.get("userId"):
        doc.update_by = actor["userId"]
    doc.updated_at = utcnow()
    await db.commit()
    content = await _load_content(doc.content_id)
    await _safe_publish(doc)
    logger.info("文档发布成功：documentId=%s", doc_id)
    return {**serializers.document_vo(doc), "content": content}


async def archive(db: AsyncSession, doc_id: str, actor: dict) -> dict:
    doc = await _find_or_throw(db, doc_id)
    _assert_writable(doc, actor)
    if not can_archive(doc.status):
        raise BadRequestError("只有已发布文档可以归档")
    doc.status = DocumentStatus.ARCHIVED
    doc.update_by = actor["userId"]
    doc.updated_at = utcnow()
    await db.commit()
    await _safe_unpublish(doc_id)
    logger.info("文档已归档：documentId=%s", doc_id)
    return serializers.document_vo(doc)


async def save_as_draft(db: AsyncSession, doc_id: str, actor: dict) -> dict:
    doc = await _find_or_throw(db, doc_id)
    _assert_writable(doc, actor)
    if doc.status != DocumentStatus.PUBLISHED:
        raise BadRequestError("只有已发布文档可以保存为草稿")
    doc.status = DocumentStatus.DRAFT
    doc.update_by = actor["userId"]
    doc.updated_at = utcnow()
    await db.commit()
    await _safe_unpublish(doc_id)
    logger.info("文档已保存为草稿：documentId=%s", doc_id)
    return serializers.document_vo(doc)


async def remove(db: AsyncSession, doc_id: str, actor: dict) -> dict:
    doc = await _find_or_throw(db, doc_id)
    _assert_writable(doc, actor)

    if doc.status == DocumentStatus.PUBLISHED:
        await _safe_unpublish(doc_id)

    doc.deleted = True
    doc.update_by = actor["userId"]
    doc.updated_at = utcnow()
    await db.commit()
    await mongo.soft_delete_content(doc.content_id)
    return {"id": doc_id, "deleted": True}


async def upload_and_create_document(
    db: AsyncSession, *, file_name: str, data: bytes, mimetype: str, meta: dict, actor: dict
) -> dict:
    if not data:
        raise BadRequestError("文件不能为空")

    original_filename = decode_upload_filename(file_name)
    extension = get_extension(original_filename)
    if not file_parser.is_supported(extension):
        raise BadRequestError(
            f"不支持的文件格式: {extension}，支持的格式: {file_parser.supported_list()}"
        )

    logger.info("上传并解析文件：name=%s, size=%d, ext=%s", original_filename, len(data), extension)

    # 解析可能长达数十秒，期间不占用池化连接（否则空闲连接被网络层断掉后
    # commit 报 connection is closed）。close 只归还连接，session 仍可用，
    # 后续 create() 会经 pool_pre_ping 重新取一条健康连接。
    await db.close()

    try:
        parsed_content = await file_parser.parse(
            original_filename,
            data,
            rustfs_upload=rustfs.upload_bytes,
            rustfs_enabled=rustfs.is_enabled(),
        )
    except BadRequestError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("文件解析失败：name=%s, error=%s", original_filename, e)
        raise BadRequestError(f"文件解析失败: {e}")

    file_url: str | None = None
    if rustfs.is_enabled():
        try:
            file_url = await rustfs.upload_bytes(
                data,
                file_name=original_filename,
                content_type=mimetype or "application/octet-stream",
                prefix="documents",
            )
        except Exception as e:  # noqa: BLE001
            logger.error("原文件上传 RustFS 失败：%s", e)
            raise BadRequestError(f"原文件上传失败: {e}")
    else:
        logger.warning("RustFS 未启用，跳过原文件上传")

    title = title_from_filename(original_filename)

    created = await create(
        db,
        {
            "title": title,
            "content": parsed_content,
            "categoryId": meta.get("categoryId"),
            "teamId": meta.get("teamId"),
            "tags": meta.get("tags"),
            "remark": meta.get("remark"),
            "isPublic": meta.get("isPublic"),
            "status": DocumentStatus.DRAFT,
        },
        actor,
    )

    return {
        "documentId": created["id"],
        "title": title,
        "fileUrl": file_url,
        "fileSize": len(data),
        "fileExtension": extension,
        "contentLength": len(parsed_content),
        "contentPreview": parsed_content[:200],
        "status": DocumentStatus.DRAFT,
    }


# ---------------- 内部助手 ----------------


async def _sync_pipeline_after_update(
    doc, old_status: int, new_status: int, content_changed: bool, visibility_changed: bool
) -> None:
    was_published = old_status == DocumentStatus.PUBLISHED
    is_published = new_status == DocumentStatus.PUBLISHED

    if was_published and not is_published:
        await _safe_unpublish(str(doc.id))
        return
    if is_published and content_changed and not is_require_approval():
        await _safe_publish(doc)
        return
    if is_published and visibility_changed:
        try:
            from app.modules.pipeline.orchestrator import update_visibility

            await update_visibility(doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("可见性同步失败（不影响文档保存）：documentId=%s, %s", doc.id, e)


async def _load_content(content_id: str) -> str:
    content_doc = await mongo.find_content(content_id)
    return (content_doc or {}).get("content", "")


async def _set_content_summary(content_id: str, summary: str) -> None:
    from datetime import datetime as dt, timezone as tz

    from bson import ObjectId

    try:
        oid = ObjectId(content_id)
    except Exception:
        return
    await mongo.content_collection().update_one(
        {"_id": oid}, {"$set": {"contentSummary": summary, "updatedAt": dt.now(tz.utc)}}
    )


async def _safe_publish(doc) -> None:
    try:
        from app.modules.mq import publisher

        await publisher.after_publish(doc)
    except Exception as e:  # noqa: BLE001
        logger.warning("索引投递失败（不影响文档状态）：documentId=%s, %s", doc.id, e)


async def _safe_unpublish(document_id: str) -> None:
    try:
        from app.modules.mq import publisher

        await publisher.after_unpublish(document_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("索引清理投递失败：documentId=%s, %s", document_id, e)


def _assert_writable(doc, actor: dict) -> None:
    if not can_write_document(doc, actor):
        raise ForbiddenError("无权修改该文档")


async def _find_or_throw(db: AsyncSession, doc_id: str) -> Document:
    doc = (
        await db.execute(select(Document).where(Document.id == doc_id, Document.deleted.is_(False)))
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {doc_id} not found")
    return doc
