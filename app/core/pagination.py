"""分页 DTO 基类 — page=1 / pageSize=20 / max 100，响应 {items,total,page,pageSize}。"""

from __future__ import annotations

import math
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageQuery(BaseModel):
    model_config = {"extra": "forbid", "from_attributes": True}

    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    pageSize: int


def offset_limit(page: int, page_size: int) -> tuple[int, int]:
    return (page - 1) * page_size, page_size


def clamp_page(page: int | None, page_size: int | None, max_size: int = 100) -> tuple[int, int]:
    p = max(1, page or 1)
    size = min(max_size, max(1, page_size or 20))
    return p, size


def total_pages(total: int, page_size: int) -> int:
    return math.ceil(total / page_size) if page_size else 0
