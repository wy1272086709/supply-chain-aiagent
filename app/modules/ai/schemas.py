"""AI DTO — 对应 src/ai/dto/*.ts。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatDto(BaseModel):
    model_config = {"extra": "forbid"}

    sessionId: str | None = None
    content: str = Field(min_length=1)
    topK: int = Field(default=5, ge=1, le=10)


class ChatStreamMessagePart(BaseModel):
    model_config = {"extra": "allow"}

    type: str | None = None
    text: str | None = None


class ChatStreamMessage(BaseModel):
    model_config = {"extra": "allow"}

    role: str | None = None
    parts: list[ChatStreamMessagePart] | None = None


class ChatStreamDto(BaseModel):
    """useChat Data Stream 请求体（额外字段放行）。"""

    model_config = {"extra": "allow"}

    messages: list[ChatStreamMessage]
    sessionId: str | None = None
    topK: int | None = Field(default=None, ge=1, le=10)
    id: str | None = None
    trigger: Any = None
    messageId: Any = None


class RagSearchDto(BaseModel):
    model_config = {"extra": "forbid"}

    query: str = Field(min_length=1)
    topK: int = Field(default=5, ge=1, le=20)


class QuerySessionDto(BaseModel):
    model_config = {"extra": "forbid"}

    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class CreateSessionDto(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, max_length=80)


class UpdateSessionDto(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=80)
