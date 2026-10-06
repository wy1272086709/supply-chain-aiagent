"""文档 DTO — 对应 src/document/dto/*.ts。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CreateDocumentDto(BaseModel):
    model_config = {"extra": "forbid"}

    title: str
    content: str
    summary: str | None = None
    categoryId: str | None = None
    teamId: str | None = None
    coverImage: str | None = None
    tags: str | None = None
    status: int | None = Field(default=None, ge=0, le=3)
    remark: str | None = None
    isPublic: bool | None = None


class UpdateDocumentDto(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = None
    content: str | None = None
    summary: str | None = None
    categoryId: str | None = None
    teamId: str | None = None
    coverImage: str | None = None
    tags: str | None = None
    status: int | None = Field(default=None, ge=0, le=3)
    remark: str | None = None
    isPublic: bool | None = None


class QueryDocumentDto(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = None
    categoryId: str | None = None
    teamId: str | None = None
    authorId: str | None = None
    status: int | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class QueryReviewTasksDto(BaseModel):
    model_config = {"extra": "forbid"}

    status: Literal["pending", "approved", "rejected"] | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class ReviewDecisionDto(BaseModel):
    model_config = {"extra": "forbid"}

    reviewComment: str | None = None
