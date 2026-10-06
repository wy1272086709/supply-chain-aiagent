"""用户管理路由 — 对应 src/user/user.controller.ts（字面量路由先于 :id）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.constants import ROLE_ADMIN, PERMISSION_SYSTEM_USER
from app.modules.auth.deps import CurrentUser, DbSession, require_roles, require_roles_or_permissions
from app.modules.auth.schemas import (
    AssignPermissionIdsDto,
    AssignRolesDto,
    ChangePasswordDto,
    CreateUserDto,
    QueryUserDto,
    ResetPasswordDto,
    UpdateProfileDto,
    UpdateUserDto,
)
from app.modules.users import permission_service, service

router = APIRouter(prefix="/users", tags=["users"])

AdminUser = Depends(require_roles(ROLE_ADMIN))
AdminWithUserPerm = Depends(require_roles_or_permissions(ROLE_ADMIN, permissions=(PERMISSION_SYSTEM_USER,)))


@router.put("/me")
async def update_me(dto: UpdateProfileDto, user: CurrentUser, db: DbSession):
    return await service.update_profile(db, user["userId"], dto.model_dump())


@router.get("/me/stats")
async def my_stats(user: CurrentUser, db: DbSession):
    return await service.get_user_statistics(db, user["userId"])


@router.put("/password/change")
async def change_password(dto: ChangePasswordDto, user: CurrentUser, db: DbSession):
    await service.change_password(db, user["userId"], dto.oldPassword, dto.newPassword)
    return {"message": "密码修改成功"}


@router.get("/page")
async def page_users(query: Annotated[QueryUserDto, Query()], db: DbSession, user=AdminUser):
    return await service.page_users(db, query.model_dump())


@router.get("/{user_id}")
async def get_user(user_id: str, db: DbSession, user=AdminUser):
    return await service.get_user_vo(db, user_id)


@router.post("")
async def create_user(dto: CreateUserDto, db: DbSession, user=AdminUser):
    user_id = await service.create_user(
        db,
        username=dto.username,
        password=dto.password,
        email=dto.email,
        real_name=dto.realName,
        avatar=dto.avatar,
        status=dto.status,
        role_codes=dto.roleCodes,
    )
    return await service.get_user_vo(db, user_id)


@router.put("/{user_id}")
async def update_user(user_id: str, dto: UpdateUserDto, db: DbSession, user=AdminUser):
    return await service.update_user(db, user_id, dto.model_dump())


@router.delete("/{user_id}")
async def delete_user(user_id: str, db: DbSession, user=AdminUser):
    await service.delete_user(db, user_id)
    return {"message": "删除成功"}


@router.put("/{user_id}/password/reset")
async def reset_password(user_id: str, dto: ResetPasswordDto, db: DbSession, user=AdminUser):
    await service.reset_password(db, user_id, dto.newPassword)
    return {"message": "密码重置成功"}


@router.get("/{user_id}/roles")
async def get_user_roles(user_id: str, db: DbSession, user=AdminUser):
    role_codes = await service.get_user_role_codes(db, user_id)
    return {"userId": user_id, "roleCodes": role_codes}


@router.put("/{user_id}/roles")
async def assign_roles(user_id: str, dto: AssignRolesDto, db: DbSession, user=AdminWithUserPerm):
    role_codes = await service.replace_roles(db, user_id, dto.roleCodes)
    await db.commit()
    return {"userId": user_id, "roleCodes": role_codes}


@router.get("/{user_id}/permissions")
async def get_user_permissions(user_id: str, db: DbSession, user=AdminWithUserPerm):
    await service.find_by_id_or_throw(db, user_id)
    permission_codes = await permission_service.get_user_permission_codes(db, user_id)
    permission_ids = await permission_service.get_user_direct_permission_ids(db, user_id)
    return {
        "userId": user_id,
        "permissionCodes": permission_codes,
        "directPermissionIds": permission_ids,
    }


@router.put("/{user_id}/permissions")
async def assign_user_permissions(
    user_id: str, dto: AssignPermissionIdsDto, db: DbSession, user=AdminWithUserPerm
):
    await service.find_by_id_or_throw(db, user_id)
    permission_ids = await permission_service.assign_user_permissions(
        db, user_id, dto.permissionIds
    )
    return {"userId": user_id, "permissionIds": permission_ids}
