"""角色服务 — 对应 src/user/role.service.ts。"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.snowflake import next_snowflake_id
from app.db.models import Role, UserRole
from app.modules.users import serializers


async def list_all(db: AsyncSession) -> list[dict]:
    roles = (
        (await db.execute(select(Role).where(Role.status == 1).order_by(Role.role_name.asc())))
        .scalars()
        .all()
    )
    return [serializers.role_vo(r) for r in roles]


async def get_by_id(db: AsyncSession, role_id: str) -> dict:
    role = (
        await db.execute(select(Role).where(Role.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise NotFoundError("角色不存在")
    return serializers.role_vo(role)


async def create(db: AsyncSession, dto: dict) -> dict:
    exists = (
        await db.execute(select(Role).where(Role.role_code == dto["roleCode"]))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError("角色编码已存在")
    role = Role(
        id=next_snowflake_id(),
        role_name=dto["roleName"],
        role_code=dto["roleCode"],
        description=dto.get("description"),
        status=1,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return serializers.role_vo(role)


async def update(db: AsyncSession, role_id: str, dto: dict) -> dict:
    role = (
        await db.execute(select(Role).where(Role.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise NotFoundError("角色不存在")
    if dto.get("roleName") is not None:
        role.role_name = dto["roleName"]
    if dto.get("description") is not None:
        role.description = dto["description"]
    if dto.get("status") is not None:
        role.status = dto["status"]
    await db.commit()
    return serializers.role_vo(role)


async def delete(db: AsyncSession, role_id: str) -> None:
    role = (
        await db.execute(select(Role).where(Role.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise NotFoundError("角色不存在")
    bound = (
        await db.execute(
            select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)
        )
    ).scalar_one()
    if bound > 0:
        raise BadRequestError("角色仍有关联用户，无法删除")
    await db.execute(sa_delete(UserRole).where(UserRole.role_id == role_id))
    await db.delete(role)
    await db.commit()
