"""角色管理路由 — 对应 src/user/role.controller.ts（类级 @Roles(ADMIN)）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.common.constants import ROLE_ADMIN, PERMISSION_SYSTEM_ROLE
from app.modules.auth.deps import DbSession, require_roles_or_permissions
from app.modules.auth.schemas import AssignPermissionIdsDto, CreateRoleDto, UpdateRoleDto
from app.modules.users import permission_service, role_service

router = APIRouter(prefix="/roles", tags=["roles"])

AdminOnly = Depends(require_roles_or_permissions(ROLE_ADMIN))
AdminWithRolePerm = Depends(
    require_roles_or_permissions(ROLE_ADMIN, permissions=(PERMISSION_SYSTEM_ROLE,))
)


@router.get("/list")
async def list_roles(db: DbSession, user=AdminOnly):
    return await role_service.list_all(db)


@router.get("/{role_id}")
async def get_role(role_id: str, db: DbSession, user=AdminOnly):
    return await role_service.get_by_id(db, role_id)


@router.post("")
async def create_role(dto: CreateRoleDto, db: DbSession, user=AdminOnly):
    return await role_service.create(db, dto.model_dump())


@router.put("/{role_id}")
async def update_role(role_id: str, dto: UpdateRoleDto, db: DbSession, user=AdminOnly):
    return await role_service.update(db, role_id, dto.model_dump(exclude_none=True))


@router.delete("/{role_id}")
async def delete_role(role_id: str, db: DbSession, user=AdminOnly):
    await role_service.delete(db, role_id)
    return {"message": "删除成功"}


@router.get("/{role_id}/permissions")
async def get_role_permissions(role_id: str, db: DbSession, user=AdminWithRolePerm):
    permission_ids = await permission_service.get_role_permission_ids(db, role_id)
    return {"roleId": role_id, "permissionIds": permission_ids}


@router.put("/{role_id}/permissions")
async def assign_role_permissions(
    role_id: str, dto: AssignPermissionIdsDto, db: DbSession, user=AdminWithRolePerm
):
    permission_ids = await permission_service.assign_role_permissions(
        db, role_id, dto.permissionIds
    )
    return {"roleId": role_id, "permissionIds": permission_ids}
