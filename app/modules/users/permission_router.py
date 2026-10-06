"""权限管理路由 — 对应 src/user/permission.controller.ts。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.constants import (
    ROLE_ADMIN,
    PERMISSION_SYSTEM_PERMISSION,
    PERMISSION_SYSTEM_PERMISSION_CREATE,
    PERMISSION_SYSTEM_PERMISSION_EDIT,
    PERMISSION_SYSTEM_PERMISSION_DELETE,
)
from app.modules.auth.deps import DbSession, require_roles_or_permissions
from app.modules.auth.schemas import AssignPermissionIdsDto
from app.modules.users import permission_service
from app.modules.users.permission_schemas import (
    CreatePermissionDto,
    QueryPermissionDto,
    UpdatePermissionDto,
)

router = APIRouter(prefix="/permissions", tags=["permissions"])

AdminOnly = Depends(require_roles_or_permissions(ROLE_ADMIN))
AdminWithPerm = Depends(
    require_roles_or_permissions(ROLE_ADMIN, permissions=(PERMISSION_SYSTEM_PERMISSION,))
)
AdminWithPermCreate = Depends(
    require_roles_or_permissions(ROLE_ADMIN, permissions=(PERMISSION_SYSTEM_PERMISSION_CREATE,))
)
AdminWithPermEdit = Depends(
    require_roles_or_permissions(ROLE_ADMIN, permissions=(PERMISSION_SYSTEM_PERMISSION_EDIT,))
)
AdminWithPermDelete = Depends(
    require_roles_or_permissions(ROLE_ADMIN, permissions=(PERMISSION_SYSTEM_PERMISSION_DELETE,))
)


@router.get("/list")
async def list_permissions(db: DbSession, user=AdminWithPerm):
    return await permission_service.list_all(db)


@router.get("/tree")
async def permission_tree(db: DbSession, user=AdminWithPerm):
    return await permission_service.get_tree(db)


@router.get("/page")
async def page_permissions(query: Annotated[QueryPermissionDto, Query()], db: DbSession, user=AdminWithPerm):
    return await permission_service.page(db, query.model_dump())


@router.get("/{perm_id}/children")
async def permission_children(perm_id: str, db: DbSession, user=AdminWithPerm):
    return await permission_service.get_children(db, perm_id)


@router.get("/{perm_id}")
async def get_permission(perm_id: str, db: DbSession, user=AdminWithPerm):
    perm = await permission_service.get_by_id(db, perm_id)
    from app.modules.users.serializers import permission_vo

    return permission_vo(perm)


@router.post("")
async def create_permission(dto: CreatePermissionDto, db: DbSession, user=AdminWithPermCreate):
    return await permission_service.create(db, dto.model_dump())


@router.put("/{perm_id}")
async def update_permission(perm_id: str, dto: UpdatePermissionDto, db: DbSession, user=AdminWithPermEdit):
    return await permission_service.update(db, perm_id, dto.model_dump(exclude_none=True))


@router.delete("/{perm_id}")
async def delete_permission(perm_id: str, db: DbSession, user=AdminWithPermDelete):
    await permission_service.delete(db, perm_id)
    return {"message": "删除成功"}
