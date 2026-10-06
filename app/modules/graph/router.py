"""图谱路由 — 对应 src/graph/graph.controller.ts（类级 search 权限）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.common.constants import PERMISSION_SEARCH
from app.modules.auth.deps import CurrentUser, require_permissions
from app.modules.documents.access import access_from_user
from app.modules.pipeline import graph_build

router = APIRouter(prefix="/graph", tags=["graph"])

SearchPerm = Depends(require_permissions(PERMISSION_SEARCH))


class GraphQueryDto(BaseModel):
    model_config = {"extra": "forbid"}

    type: str | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)


class GraphSearchDto(BaseModel):
    model_config = {"extra": "forbid"}

    keyword: str = Field(min_length=1)
    limit: int | None = Field(default=None, ge=1, le=200)


class GraphOverviewDto(BaseModel):
    model_config = {"extra": "forbid"}

    keyword: str | None = None
    entityType: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    docLimit: int | None = Field(default=None, ge=1, le=80)

    model_config = {"extra": "forbid", "populate_by_name": True}


@router.get("/overview")
async def overview(query: Annotated[GraphOverviewDto, Query()], user: CurrentUser, _: object = SearchPerm):
    return await graph_build.get_overview(
        keyword=query.keyword,
        entity_type=query.entityType,
        from_=query.from_,
        to=query.to,
        doc_limit=query.docLimit if query.docLimit is not None else 24,
        scope=access_from_user(user),
    )


@router.get("/search")
async def graph_search(query: Annotated[GraphSearchDto, Query()], user: CurrentUser, _: object = SearchPerm):
    return await graph_build.search_graph(
        query.keyword, query.limit if query.limit is not None else 50, access_from_user(user)
    )


@router.get("/nodes")
async def graph_nodes(query: Annotated[GraphQueryDto, Query()], user: CurrentUser, _: object = SearchPerm):
    return await graph_build.list_nodes(
        query.type, query.limit if query.limit is not None else 200, access_from_user(user)
    )


@router.get("/edges")
async def graph_edges(query: Annotated[GraphQueryDto, Query()], user: CurrentUser, _: object = SearchPerm):
    return await graph_build.list_edges(
        query.limit if query.limit is not None else 500, access_from_user(user)
    )
