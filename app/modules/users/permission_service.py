"""权限服务 — 对应 src/user/permission.service.ts。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.constants import ADMIN_OPERATION_PERMISSIONS, ADMIN_ROLES
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.snowflake import next_snowflake_id
from app.db.models import Permission, Role, RolePermission, UserPermission, UserRole
from app.modules.users import serializers
from app.common.now import utcnow


async def list_all(db: AsyncSession) -> list[dict]:
    perms = (
        (
            await db.execute(
                select(Permission)
                .where(Permission.deleted.is_(False), Permission.status == 1)
                .order_by(Permission.sort.asc(), Permission.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [serializers.permission_vo(p) for p in perms]


async def get_by_id(db: AsyncSession, perm_id: str) -> Permission:
    perm = (
        await db.execute(
            select(Permission).where(Permission.id == perm_id, Permission.deleted.is_(False))
        )
    ).scalar_one_or_none()
    if not perm:
        raise NotFoundError("权限不存在")
    return perm


async def get_children(db: AsyncSession, parent_id: str) -> list[dict]:
    perms = (
        (
            await db.execute(
                select(Permission)
                .where(
                    Permission.parent_id == parent_id,
                    Permission.deleted.is_(False),
                    Permission.status == 1,
                )
                .order_by(Permission.sort.asc())
            )
        )
        .scalars()
        .all()
    )
    return [serializers.permission_vo(p) for p in perms]


async def create(db: AsyncSession, dto: dict) -> dict:
    exists = (
        await db.execute(
            select(Permission).where(
                Permission.permission_code == dto["permissionCode"],
                Permission.deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise ConflictError("权限编码已存在")
    now = utcnow()
    perm = Permission(
        id=next_snowflake_id(),
        parent_id=dto.get("parentId") or "0",
        permission_name=dto["permissionName"],
        permission_code=dto["permissionCode"],
        permission_type=dto["permissionType"],
        menu_url=dto.get("menuUrl"),
        api_url=dto.get("apiUrl"),
        method=dto.get("method"),
        icon=dto.get("icon"),
        sort=dto.get("sort") or 0,
        status=dto.get("status") if dto.get("status") is not None else 1,
        created_at=now,
        updated_at=now,
    )
    db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return serializers.permission_vo(perm)


async def update(db: AsyncSession, perm_id: str, dto: dict) -> dict:
    perm = await get_by_id(db, perm_id)
    new_code = dto.get("permissionCode")
    if new_code and new_code != perm.permission_code:
        exists = (
            await db.execute(
                select(Permission).where(
                    Permission.permission_code == new_code, Permission.deleted.is_(False)
                )
            )
        ).scalar_one_or_none()
        if exists:
            raise ConflictError("权限编码已存在")
        perm.permission_code = new_code
    field_map = {
        "permissionName": "permission_name",
        "permissionType": "permission_type",
        "menuUrl": "menu_url",
        "apiUrl": "api_url",
        "method": "method",
        "icon": "icon",
        "sort": "sort",
        "status": "status",
    }
    for field, column in field_map.items():
        if dto.get(field) is not None:
            setattr(perm, column, dto[field])
    if dto.get("parentId") is not None:
        if dto["parentId"] == perm_id:
            raise BadRequestError("父权限不能是自己")
        perm.parent_id = dto["parentId"]
    perm.updated_at = utcnow()
    await db.commit()
    return serializers.permission_vo(perm)


async def delete(db: AsyncSession, perm_id: str) -> None:
    perm = await get_by_id(db, perm_id)
    child_count = (
        await db.execute(
            select(func.count()).select_from(Permission).where(
                Permission.parent_id == perm_id, Permission.deleted.is_(False)
            )
        )
    ).scalar_one()
    if child_count > 0:
        raise BadRequestError("存在子权限，无法删除")
    role_bound = (
        await db.execute(
            select(func.count()).select_from(RolePermission).where(
                RolePermission.permission_id == perm_id
            )
        )
    ).scalar_one()
    user_bound = (
        await db.execute(
            select(func.count()).select_from(UserPermission).where(
                UserPermission.permission_id == perm_id
            )
        )
    ).scalar_one()
    if role_bound > 0 or user_bound > 0:
        raise BadRequestError("权限仍有关联角色或用户，无法删除")
    perm.deleted = True
    perm.updated_at = utcnow()
    await db.commit()


async def page(db: AsyncSession, query: dict) -> dict:
    page = query.get("page") or 1
    page_size = query.get("pageSize") or 20
    base = select(Permission).where(Permission.deleted.is_(False))
    count_q = select(func.count()).select_from(Permission).where(Permission.deleted.is_(False))
    keyword = (query.get("keyword") or "").strip()
    if keyword:
        kw = f"%{keyword}%"
        cond = Permission.permission_name.ilike(kw) | Permission.permission_code.ilike(kw)
        base = base.where(cond)
        count_q = count_q.where(cond)
    total = (await db.execute(count_q)).scalar_one()
    perms = (
        (
            await db.execute(
                base.order_by(Permission.sort.asc(), Permission.created_at.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [serializers.permission_vo(p) for p in perms],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_tree(db: AsyncSession) -> list[dict]:
    perms = await list_all(db)

    def build(parent_id: str) -> list[dict]:
        return [
            {**p, "children": build(p["id"])} for p in perms if p["parentId"] == parent_id
        ]

    return build("0")


async def get_role_permission_ids(db: AsyncSession, role_id: str) -> list[str]:
    await _ensure_role_exists(db, role_id)
    rows = await db.execute(
        select(RolePermission.permission_id).where(RolePermission.role_id == role_id)
    )
    return [str(r[0]) for r in rows.all()]


async def assign_role_permissions(db: AsyncSession, role_id: str, permission_ids: list[str]) -> list[str]:
    await _ensure_role_exists(db, role_id)
    await _validate_permission_ids(db, permission_ids)
    await db.execute(sa_delete(RolePermission).where(RolePermission.role_id == role_id))
    for pid in permission_ids:
        db.add(
            RolePermission(
                id=next_snowflake_id(), role_id=role_id, permission_id=pid,
                created_at=utcnow(),
            )
        )
    await db.commit()
    return permission_ids


async def get_user_direct_permission_ids(db: AsyncSession, user_id: str) -> list[str]:
    rows = await db.execute(
        select(UserPermission.permission_id).where(UserPermission.user_id == user_id)
    )
    return [str(r[0]) for r in rows.all()]


async def assign_user_permissions(db: AsyncSession, user_id: str, permission_ids: list[str]) -> list[str]:
    await _validate_permission_ids(db, permission_ids)
    await db.execute(sa_delete(UserPermission).where(UserPermission.user_id == user_id))
    for pid in permission_ids:
        db.add(
            UserPermission(
                id=next_snowflake_id(), user_id=user_id, permission_id=pid,
                created_at=utcnow(),
            )
        )
    await db.commit()
    return permission_ids


async def get_user_permission_codes(db: AsyncSession, user_id: str) -> list[str]:
    """直接权限 + 角色权限；管理员追加 ADMIN_OPERATION_PERMISSIONS。"""
    direct = await db.execute(
        select(Permission.permission_code)
        .join(UserPermission, Permission.id == UserPermission.permission_id)
        .where(
            UserPermission.user_id == user_id,
            Permission.status == 1,
            Permission.deleted.is_(False),
        )
    )
    via_role = await db.execute(
        select(Permission.permission_code.distinct())
        .join(RolePermission, Permission.id == RolePermission.permission_id)
        .join(UserRole, RolePermission.role_id == UserRole.role_id)
        .where(
            UserRole.user_id == user_id,
            Permission.status == 1,
            Permission.deleted.is_(False),
        )
    )
    codes = {r[0] for r in direct.all()} | {r[0] for r in via_role.all()}

    role_codes = await db.execute(
        select(Role.role_code)
        .join(UserRole, Role.id == UserRole.role_id)
        .where(UserRole.user_id == user_id, Role.status == 1)
    )
    is_admin = any(r[0] in ADMIN_ROLES for r in role_codes.all())
    if is_admin:
        codes.update(ADMIN_OPERATION_PERMISSIONS)
    return sorted(codes)


async def _ensure_role_exists(db: AsyncSession, role_id: str) -> None:
    role = (
        await db.execute(select(Role).where(Role.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise NotFoundError("角色不存在")


async def _validate_permission_ids(db: AsyncSession, ids: list[str]) -> None:
    if not ids:
        return
    found = (
        (
            await db.execute(
                select(Permission).where(
                    Permission.id.in_(ids), Permission.deleted.is_(False), Permission.status == 1
                )
            )
        )
        .scalars()
        .all()
    )
    if len(found) != len(set(ids)):
        raise NotFoundError("部分权限不存在或已禁用")
