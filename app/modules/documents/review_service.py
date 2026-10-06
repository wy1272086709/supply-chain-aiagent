"""文档发布审核服务 — 对应 src/document/document-review.service.ts。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.core.snowflake import next_snowflake_id
from app.db.models import Document, DocumentReview
from app.modules.documents import service as document_service
from app.modules.documents.access import can_write_document
from app.modules.documents.status import DocumentStatus, can_submit_review
from app.modules.users import serializers
from app.common.now import utcnow

logger = logging.getLogger(__name__)

REVIEW_RESULT_APPROVED = 1
REVIEW_RESULT_REJECTED = 2


def is_require_approval() -> bool:
    return document_service.is_require_approval()


async def submit_for_review(db: AsyncSession, document_id: str, actor: dict | None = None) -> Document:
    doc = await _find_document_or_throw(db, document_id)
    if actor and not can_write_document(doc, actor):
        raise ForbiddenError("无权提交该文档审核")

    if not can_submit_review(doc.status):
        raise BadRequestError("只有草稿或已发布状态的文档才能提交审核")

    pending = (
        await db.execute(
            select(DocumentReview).where(
                DocumentReview.document_id == document_id,
                DocumentReview.review_result.is_(None),
            )
        )
    ).scalar_one_or_none()
    if pending:
        raise BadRequestError("该文档已有待审核任务")

    before_status = doc.status
    review = DocumentReview(
        id=next_snowflake_id(),
        document_id=document_id,
        before_status=before_status,
        created_at=utcnow(),
    )
    db.add(review)
    await db.flush()

    doc.status = DocumentStatus.PENDING_REVIEW
    if actor and actor.get("userId"):
        doc.update_by = actor["userId"]
    doc.updated_at = utcnow()
    await db.commit()

    if before_status == DocumentStatus.PUBLISHED:
        await document_service._safe_unpublish(document_id)

    logger.info(
        "文档已提交审核：documentId=%s, reviewId=%s, beforeStatus=%s",
        document_id, review.id, before_status,
    )
    return doc


async def approve_review(
    db: AsyncSession,
    review_id: str,
    reviewer_id: str,
    reviewer_name: str,
    review_comment: str | None = None,
) -> dict:
    review = await _find_pending_review_or_throw(db, review_id)

    review.review_result = REVIEW_RESULT_APPROVED
    review.reviewer_id = reviewer_id
    review.reviewer_name = reviewer_name
    review.review_comment = review_comment
    review.reviewed_at = utcnow()

    doc = await _find_document_or_throw(db, review.document_id)
    doc.status = DocumentStatus.PUBLISHED
    doc.publish_time = utcnow()
    doc.updated_at = utcnow()
    await db.commit()
    await document_service._safe_publish(doc)

    logger.info("审核通过：reviewId=%s, documentId=%s", review_id, doc.id)
    return serializers.document_vo(doc)


async def reject_review(
    db: AsyncSession,
    review_id: str,
    review_comment: str,
    reviewer_id: str,
    reviewer_name: str,
) -> dict:
    if not (review_comment or "").strip():
        raise BadRequestError("驳回意见不能为空")

    review = await _find_pending_review_or_throw(db, review_id)

    review.review_result = REVIEW_RESULT_REJECTED
    review.reviewer_id = reviewer_id
    review.reviewer_name = reviewer_name
    review.review_comment = review_comment.strip()
    review.reviewed_at = utcnow()

    doc = await _find_document_or_throw(db, review.document_id)
    doc.status = DocumentStatus.DRAFT
    doc.updated_at = utcnow()
    await db.commit()

    logger.info("审核驳回：reviewId=%s, documentId=%s", review_id, doc.id)
    return serializers.document_vo(doc)


async def list_tasks(db: AsyncSession, query: dict) -> dict:
    page = query.get("page") or 1
    page_size = query.get("pageSize") or 20
    status_filter = query.get("status")

    base = select(DocumentReview)
    count_q = select(func.count()).select_from(DocumentReview)
    if status_filter == "pending" or not status_filter:
        cond = DocumentReview.review_result.is_(None)
        base = base.where(cond)
        count_q = count_q.where(cond)
    elif status_filter == "approved":
        cond = DocumentReview.review_result == REVIEW_RESULT_APPROVED
        base = base.where(cond)
        count_q = count_q.where(cond)
    elif status_filter == "rejected":
        cond = DocumentReview.review_result == REVIEW_RESULT_REJECTED
        base = base.where(cond)
        count_q = count_q.where(cond)

    total = (await db.execute(count_q)).scalar_one()
    reviews = (
        (
            await db.execute(
                base.order_by(DocumentReview.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [serializers.review_vo(r) for r in reviews],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_pending_count(db: AsyncSession) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(DocumentReview).where(
                DocumentReview.review_result.is_(None)
            )
        )
    ).scalar_one()


async def get_current_review(db: AsyncSession, document_id: str) -> dict | None:
    review = (
        await db.execute(
            select(DocumentReview)
            .where(DocumentReview.document_id == document_id, DocumentReview.review_result.is_(None))
            .order_by(DocumentReview.created_at.desc())
        )
    ).scalar_one_or_none()
    return serializers.review_vo(review) if review else None


async def get_review_history(db: AsyncSession, document_id: str) -> list[dict]:
    reviews = (
        (
            await db.execute(
                select(DocumentReview)
                .where(DocumentReview.document_id == document_id)
                .order_by(DocumentReview.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [serializers.review_vo(r) for r in reviews]


async def _find_pending_review_or_throw(db: AsyncSession, review_id: str) -> DocumentReview:
    review = (
        await db.execute(select(DocumentReview).where(DocumentReview.id == review_id))
    ).scalar_one_or_none()
    if not review:
        raise NotFoundError(f"Review {review_id} not found")
    if review.review_result is not None:
        raise BadRequestError("该审核任务已处理")
    return review


async def _find_document_or_throw(db: AsyncSession, document_id: str) -> Document:
    doc = (
        await db.execute(select(Document).where(Document.id == document_id, Document.deleted.is_(False)))
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")
    return doc
