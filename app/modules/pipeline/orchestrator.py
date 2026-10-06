"""发布后知识管线编排器 — 对应 pipeline.orchestrator.ts。

RAG：分块→Embedding→ES kh_chunk；Search：全文→ES kh_document；KG：抽实体→Neo4j。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.db import mongo
from app.db.models import Document
from app.db.postgres import get_session_factory
from app.modules.documents.status import DocumentStatus
from app.modules.pipeline import chunking, embedding, search_index, vector_index

logger = logging.getLogger(__name__)


def _to_iso_date(value) -> str | None:
    """ES date 字段需要 ISO-8601。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.isoformat()
    except ValueError:
        return None


def _to_pipeline_doc(doc: Document, content: str) -> dict:
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": content,
        "summary": doc.summary,
        "categoryId": doc.category_id,
        "authorId": doc.author_id,
        "teamId": doc.team_id,
        "status": doc.status,
        "tags": doc.tags,
        "isPublic": doc.is_public,
        "viewCount": doc.view_count,
        "likeCount": doc.like_count,
        "commentCount": doc.comment_count,
        "publishTime": doc.publish_time,
        "createdAt": doc.created_at,
        "updatedAt": doc.updated_at,
    }


def _to_search_index_doc(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "title": doc["title"],
        "summary": doc.get("summary"),
        "content": doc.get("content") or "",
        "categoryId": doc.get("categoryId"),
        "tags": doc.get("tags"),
        "status": doc["status"],
        "isPublic": doc["isPublic"],
        "viewCount": doc.get("viewCount", 0),
        "likeCount": doc.get("likeCount", 0),
        "commentCount": doc.get("commentCount", 0),
        "authorId": doc.get("authorId"),
        "teamId": doc.get("teamId"),
        "publishTime": _to_iso_date(doc.get("publishTime")),
        "createdAt": _to_iso_date(doc.get("createdAt")),
        "updatedAt": _to_iso_date(doc.get("updatedAt")),
    }


async def _load_documents_by_ids(ids: list[str]) -> list[dict]:
    """按 ID 列表加载 PG 元数据 + Mongo 正文。"""
    factory = get_session_factory()
    result: list[dict] = []
    async with factory() as db:
        for doc_id in ids:
            doc = (
                await db.execute(
                    select(Document).where(Document.id == doc_id, Document.deleted.is_(False))
                )
            ).scalar_one_or_none()
            if not doc:
                continue
            content_doc = await mongo.find_content(doc.content_id)
            result.append(_to_pipeline_doc(doc, (content_doc or {}).get("content", "")))
    return result


async def _load_all_published_documents() -> list[dict]:
    factory = get_session_factory()
    result: list[dict] = []
    async with factory() as db:
        docs = (
            (
                await db.execute(
                    select(Document).where(
                        Document.deleted.is_(False), Document.status == DocumentStatus.PUBLISHED
                    )
                )
            )
            .scalars()
            .all()
        )
        for doc in docs:
            content_doc = await mongo.find_content(doc.content_id)
            result.append(_to_pipeline_doc(doc, (content_doc or {}).get("content", "")))
    return result


async def handle_rag_reindex(msg_type: str, document_ids: list[str] | None) -> None:
    if msg_type == "DELETE_BY_DOC_IDS" and document_ids:
        for doc_id in document_ids:
            await vector_index.delete_by_doc_id(doc_id)
        return

    if msg_type != "BY_DOC_IDS" or not document_ids:
        logger.warning("忽略未支持的 RAG 消息：type=%s", msg_type)
        return

    docs = await _load_documents_by_ids(document_ids)
    logger.info("RAG 开始索引：type=%s, total=%d", msg_type, len(docs))
    for doc in docs:
        try:
            await _reindex_one(doc)
        except Exception as e:  # noqa: BLE001
            logger.error("RAG 索引失败：documentId=%s, %s", doc["id"], e)


async def _reindex_one(doc: dict) -> None:
    if not (doc.get("content") or "").strip():
        logger.warning("文档内容为空，跳过 RAG：documentId=%s", doc["id"])
        return

    await vector_index.delete_by_doc_id(doc["id"])

    chunks = chunking.chunk(
        content=doc["content"],
        document_id=doc["id"],
        document_title=doc["title"],
        category_id=doc.get("categoryId"),
        author_id=doc.get("authorId"),
        team_id=doc.get("teamId"),
        is_public=doc.get("isPublic"),
        doc_status=doc.get("status"),
        publish_time=_to_iso_date(doc.get("publishTime")),
    )
    if not chunks:
        return

    embeddings = await embedding.embed_batch([c["content"] for c in chunks])
    for i, c in enumerate(chunks):
        c["embedding"] = embeddings[i]

    await vector_index.index_chunks(chunks)
    logger.info("RAG 索引完成：documentId=%s, chunks=%d", doc["id"], len(chunks))


async def update_visibility(doc) -> None:
    """已发布文档改公开/所属团队：只同步三套索引可见性字段。"""
    from app.modules.pipeline import graph_build

    vis = {
        "isPublic": doc.is_public if doc.is_public is not None else False,
        "teamId": str(doc.team_id) if doc.team_id is not None else None,
        "authorId": str(doc.author_id) if doc.author_id is not None else None,
    }
    logger.info(
        "同步可见性：documentId=%s, isPublic=%s, teamId=%s", doc.id, vis["isPublic"], vis["teamId"] or "-"
    )
    await asyncio.gather(
        search_index.update_visibility(str(doc.id), vis),
        vector_index.update_visibility(str(doc.id), vis),
        graph_build.update_visibility(str(doc.id), vis),
    )


async def handle_search_index(msg_type: str, document_id: str) -> None:
    if msg_type == "DELETE":
        await search_index.delete_document(document_id)
        return
    if msg_type == "INDEX":
        docs = await _load_documents_by_ids([document_id])
        if not docs:
            logger.warning("Search INDEX 文档不存在：documentId=%s", document_id)
            return
        await search_index.index_document(_to_search_index_doc(docs[0]))
        return
    logger.warning("忽略未支持的 Search 消息：type=%s", msg_type)


async def handle_kg_build(msg_type: str, document_ids: list[str] | None) -> None:
    from app.modules.pipeline import graph_build

    if msg_type == "DELETE_BY_DOC_IDS" and document_ids:
        for doc_id in document_ids:
            await graph_build.delete_for_document(doc_id)
        return

    if msg_type == "BUILD_BY_DOC_IDS" and document_ids:
        docs = await _load_documents_by_ids(document_ids)
    elif msg_type == "BUILD_ALL":
        docs = await _load_all_published_documents()
    else:
        docs = []

    if not docs:
        logger.warning("忽略未支持或空的 KG 消息：type=%s", msg_type)
        return

    logger.info("KG 开始建图：type=%s, total=%d", msg_type, len(docs))
    await graph_build.build_batch(docs)
