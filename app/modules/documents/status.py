"""文档状态与流转规则 — 对应 src/document/document-status.ts。"""

from __future__ import annotations

from enum import IntEnum


class DocumentStatus(IntEnum):
    DRAFT = 0
    PUBLISHED = 1
    ARCHIVED = 2
    PENDING_REVIEW = 3


DOCUMENT_STATUS_LABEL = {
    DocumentStatus.DRAFT: "草稿",
    DocumentStatus.PUBLISHED: "已发布",
    DocumentStatus.ARCHIVED: "已归档",
    DocumentStatus.PENDING_REVIEW: "待审核",
}


def can_publish_from(status: int) -> bool:
    return status in (
        DocumentStatus.DRAFT,
        DocumentStatus.PUBLISHED,
        DocumentStatus.ARCHIVED,
        DocumentStatus.PENDING_REVIEW,
    )


def can_edit_content(status: int) -> bool:
    return status in (DocumentStatus.DRAFT, DocumentStatus.PUBLISHED, DocumentStatus.ARCHIVED)


def can_archive(status: int) -> bool:
    return status == DocumentStatus.PUBLISHED


def can_submit_review(status: int) -> bool:
    return status in (DocumentStatus.DRAFT, DocumentStatus.PUBLISHED)
