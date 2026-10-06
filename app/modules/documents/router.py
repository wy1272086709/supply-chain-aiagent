"""文档路由 — 对应 src/document/document.controller.ts（字面量路由先于 :id）。"""

from __future__ import annotations

from typing import Annotated

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.common.constants import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    PERMISSION_DOCUMENT_CREATE,
    PERMISSION_DOCUMENT_DELETE,
    PERMISSION_DOCUMENT_EDIT,
    PERMISSION_DOCUMENT_LIST,
    PERMISSION_DOCUMENT_REVIEW,
)
from app.core.errors import BadRequestError, PayloadTooLargeError
from app.modules.auth.deps import CurrentUser, DbSession, require_permissions, require_roles_or_permissions
from app.modules.documents import review_service, service
from app.modules.documents.schemas import (
    CreateDocumentDto,
    QueryDocumentDto,
    QueryReviewTasksDto,
    ReviewDecisionDto,
    UpdateDocumentDto,
)

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_MAX_BYTES = 50 * 1024 * 1024

CreatePerm = Depends(require_permissions(PERMISSION_DOCUMENT_CREATE))
ListPerm = Depends(require_permissions(PERMISSION_DOCUMENT_LIST))
EditPerm = Depends(require_permissions(PERMISSION_DOCUMENT_EDIT))
DeletePerm = Depends(require_permissions(PERMISSION_DOCUMENT_DELETE))
ReviewerPerm = Depends(
    require_roles_or_permissions(
        ROLE_REVIEWER, ROLE_ADMIN, permissions=(PERMISSION_DOCUMENT_REVIEW,)
    )
)
ListOrReviewPerm = Depends(
    require_permissions(PERMISSION_DOCUMENT_LIST, PERMISSION_DOCUMENT_REVIEW)
)


@router.post("")
async def create_document(dto: CreateDocumentDto, user: CurrentUser, db: DbSession):
    return await service.create(db, dto.model_dump(), user)


@router.post("/upload/parse")
async def upload_and_parse(
    file: UploadFile | None = File(default=None),
    categoryId: str | None = Form(default=None),
    teamId: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    remark: str | None = Form(default=None),
    isPublic: str | None = Form(default=None),
    user: CurrentUser = None,
    db: DbSession = None,
    _: object = CreatePerm,
):
    if file is None:
        raise BadRequestError("请上传文件（form-data 字段名: file）")

    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > UPLOAD_MAX_BYTES:
            raise PayloadTooLargeError("文件大小超过限制（50MB）")
        chunks.append(chunk)
    data = b"".join(chunks)

    is_public = isPublic in ("true", "1", "True", "TRUE") if isPublic is not None else None
    meta = {"categoryId": categoryId, "teamId": teamId, "tags": tags, "remark": remark,
            "isPublic": is_public}
    return await service.upload_and_create_document(
        db,
        file_name=file.filename or "",
        data=data,
        mimetype=file.content_type or "",
        meta=meta,
        actor=user,
    )


@router.get("/reviews/tasks")
async def list_review_tasks(query: Annotated[QueryReviewTasksDto, Query()], db: DbSession, user=ReviewerPerm):
    return await review_service.list_tasks(db, query.model_dump())


@router.get("/reviews/tasks/pending-count")
async def pending_review_count(db: DbSession, user=ReviewerPerm):
    return await review_service.get_pending_count(db)


@router.get("")
async def find_all_documents(query: Annotated[QueryDocumentDto, Query()], user: CurrentUser, db: DbSession):
    return await service.find_all(db, query.model_dump(), user)


@router.put("/{document_id}/publish")
async def publish_document(document_id: str, user: CurrentUser, db: DbSession, _: object = EditPerm):
    return await service.publish(db, document_id, user)


@router.put("/{document_id}/archive")
async def archive_document(document_id: str, user: CurrentUser, db: DbSession, _: object = EditPerm):
    return await service.archive(db, document_id, user)


@router.put("/{document_id}/save-draft")
async def save_draft(document_id: str, user: CurrentUser, db: DbSession, _: object = EditPerm):
    return await service.save_as_draft(db, document_id, user)


@router.post("/{document_id}/reviews/submit")
async def submit_review(document_id: str, user: CurrentUser, db: DbSession, _: object = EditPerm):
    from app.modules.users import serializers

    doc = await review_service.submit_for_review(db, document_id, user)
    return serializers.document_vo(doc)


@router.get("/{document_id}/reviews/current")
async def current_review(document_id: str, db: DbSession, user=ListOrReviewPerm):
    return await review_service.get_current_review(db, document_id)


@router.get("/{document_id}/reviews/history")
async def review_history(document_id: str, db: DbSession, user=ListOrReviewPerm):
    return await review_service.get_review_history(db, document_id)


@router.post("/reviews/tasks/{task_id}/approve")
async def approve_review(task_id: str, dto: ReviewDecisionDto, user: CurrentUser, db: DbSession, _: object = ReviewerPerm):
    return await review_service.approve_review(
        db, task_id, user["userId"], user.get("realName") or user["username"], dto.reviewComment
    )


@router.post("/reviews/tasks/{task_id}/reject")
async def reject_review(task_id: str, dto: ReviewDecisionDto, user: CurrentUser, db: DbSession, _: object = ReviewerPerm):
    return await review_service.reject_review(
        db, task_id, dto.reviewComment or "", user["userId"], user.get("realName") or user["username"]
    )


@router.get("/{document_id}")
async def find_document(document_id: str, user: CurrentUser, db: DbSession, _: object = ListPerm):
    return await service.find_one(db, document_id, True, user)


@router.patch("/{document_id}")
async def update_document(document_id: str, dto: UpdateDocumentDto, user: CurrentUser, db: DbSession, _: object = EditPerm):
    return await service.update(db, document_id, dto.model_dump(exclude_none=True), user)


@router.delete("/{document_id}")
async def delete_document(document_id: str, user: CurrentUser, db: DbSession, _: object = DeletePerm):
    return await service.remove(db, document_id, user)
