"""团队路由 — 对应 src/team/team.controller.ts。

成员增删的 body 是裸 JSON 数组（非对象），用 Request 手动解析。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.constants import ROLE_ADMIN, PERMISSION_SYSTEM_TEAM
from app.core.errors import BadRequestError
from app.modules.auth.deps import CurrentUser, DbSession, require_roles_or_permissions
from app.modules.auth.schemas import CreateTeamDto, QueryTeamDto, UpdateTeamDto
from app.modules.teams import service

router = APIRouter(prefix="/teams", tags=["teams"])

AdminWithTeamPerm = Depends(
    require_roles_or_permissions(ROLE_ADMIN, permissions=(PERMISSION_SYSTEM_TEAM,))
)


async def _parse_user_ids(request: Request) -> list[str]:
    try:
        body = await request.json()
    except Exception:
        raise BadRequestError("请求体必须是 JSON 数组")
    if not isinstance(body, list) or not all(isinstance(x, str) for x in body):
        raise BadRequestError("请求体必须是字符串数组")
    return body


@router.post("")
async def create_team(dto: CreateTeamDto, db: DbSession, user=AdminWithTeamPerm):
    return await service.create(db, dto.model_dump())


@router.put("/{team_id}")
async def update_team(team_id: str, dto: UpdateTeamDto, db: DbSession, user=AdminWithTeamPerm):
    return await service.update(db, team_id, dto.model_dump())


@router.delete("/{team_id}")
async def delete_team(team_id: str, db: DbSession, user=AdminWithTeamPerm):
    await service.delete(db, team_id)
    return {"message": "删除成功"}


@router.get("/page")
async def page_teams(query: Annotated[QueryTeamDto, Query()], db: DbSession, user=AdminWithTeamPerm):
    return await service.page(db, query.model_dump())


@router.get("/mine")
async def my_teams(user: CurrentUser, db: DbSession):
    return await service.list_mine(db, user["userId"])


@router.get("/tree")
async def team_tree(db: DbSession, rootOnly: str | None = None, user=AdminWithTeamPerm):
    return await service.get_tree(db, root_only=rootOnly == "true")


@router.get("/{team_id}")
async def get_team(team_id: str, db: DbSession, user=AdminWithTeamPerm):
    return await service.get_detail(db, team_id)


@router.post("/{team_id}/members")
async def add_members(team_id: str, request: Request, db: DbSession, user=AdminWithTeamPerm):
    user_ids = await _parse_user_ids(request)
    return await service.add_members(db, team_id, user_ids)


@router.delete("/{team_id}/members")
async def remove_members(team_id: str, request: Request, db: DbSession, user=AdminWithTeamPerm):
    user_ids = await _parse_user_ids(request)
    return await service.remove_members(db, team_id, user_ids)


@router.get("/{team_id}/members")
async def list_members(team_id: str, db: DbSession, user=AdminWithTeamPerm):
    return await service.list_members(db, team_id)
