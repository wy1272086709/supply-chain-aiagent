"""搜索路由 — 对应 src/search/search.controller.ts + dto/search.dto.ts。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends

from app.common.constants import PERMISSION_SEARCH
from app.modules.auth.deps import CurrentUser, DbSession, require_permissions
from app.modules.documents.access import access_from_user
from app.modules.pipeline import search_index

router = APIRouter(prefix="/search", tags=["search"])


class SearchDto(BaseModel):
    model_config = {"extra": "forbid"}

    keyword: str
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=10, ge=1, le=50)
    categoryId: str | None = None
    authorId: str | None = None


@router.post("")
async def search(dto: SearchDto, user: CurrentUser, db: DbSession,
                 _: object = Depends(require_permissions(PERMISSION_SEARCH))):
    return await search_index.search_documents(
        keyword=dto.keyword,
        page=dto.page,
        page_size=dto.pageSize,
        category_id=dto.categoryId,
        author_id=dto.authorId,
        scope=access_from_user(user),
    )
