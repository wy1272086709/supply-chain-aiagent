"""用户服务 — 对应 src/user/user.service.ts（逻辑逐字移植）。"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.constants import ROLE_USER
from app.core.errors import ConflictError, NotFoundError, UnauthorizedError, BadRequestError
from app.core.security import hash_password, verify_password
from app.core.snowflake import next_snowflake_id
from app.db.models import Document, Role, User, UserRole
from app.modules.users import serializers
from app.modules.users.permission_service import get_user_permission_codes
from app.modules.teams.service import list_accessible_team_ids
from app.common.now import utcnow


async def find_by_email(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(select(User).where(User.email == email, User.deleted.is_(False)))
    ).scalar_one_or_none()


async def find_by_username(db: AsyncSession, username: str) -> User | None:
    return (
        await db.execute(select(User).where(User.username == username, User.deleted.is_(False)))
    ).scalar_one_or_none()


async def find_by_id_or_throw(db: AsyncSession, user_id: str) -> User:
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted.is_(False)))
    ).scalar_one_or_none()
    if not user:
        raise NotFoundError("用户不存在")
    return user


async def get_role_codes(db: AsyncSession, user_id: str) -> list[str]:
    rows = await db.execute(
        select(Role.role_code)
        .join(UserRole, Role.id == UserRole.role_id)
        .where(UserRole.user_id == user_id, Role.status == 1)
    )
    return [r[0] for r in rows.all()]


async def to_user_vo(db: AsyncSession, user: User) -> dict:
    role_codes = await get_role_codes(db, user.id)
    return serializers.user_vo(user, role_codes)


async def to_auth_user(db: AsyncSession, user: User, roles: list[str], permissions: list[str]) -> dict:
    team_ids = await list_accessible_team_ids(db, user.id)
    return {
        "userId": user.id,
        "username": user.username,
        "realName": user.real_name,
        "email": user.email,
        "avatar": user.avatar,
        "roles": roles,
        "permissions": permissions,
        "teamIds": [str(t) for t in team_ids],
    }


async def build_auth_user(db: AsyncSession, user_id: str) -> dict:
    user = await find_by_id_or_throw(db, user_id)
    if user.status != 1:
        raise UnauthorizedError("账户已禁用")
    roles = await get_role_codes(db, user_id)
    permissions = await get_user_permission_codes(db, user_id)
    return await to_auth_user(db, user, roles, permissions)


async def validate_credentials(db: AsyncSession, username: str, password: str) -> dict:
    user = await find_by_username(db, username)
    if not user:
        raise UnauthorizedError("用户名或密码错误")
    if user.status != 1:
        raise UnauthorizedError("账户已禁用")
    if user.email_verified == 0:
        raise UnauthorizedError("账户未激活，请先验证邮箱")
    if not verify_password(password, user.password):
        raise UnauthorizedError("用户名或密码错误")
    roles = await get_role_codes(db, user.id)
    permissions = await get_user_permission_codes(db, user.id)
    return await to_auth_user(db, user, roles, permissions)


async def register(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    email: str | None,
    real_name: str | None,
    require_email_verification: bool,
) -> dict:
    if await find_by_username(db, username):
        raise ConflictError("用户名已存在")
    if email and await find_by_email(db, email):
        raise ConflictError("邮箱已被使用")

    user_id = next_snowflake_id()
    need_verify = require_email_verification and bool(email)
    user = User(
        id=user_id,
        username=username,
        password=hash_password(password),
        email=email,
        real_name=real_name,
        status=0 if need_verify else 1,
        email_verified=0 if need_verify else 1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(user)
    await db.flush()
    await assign_role(db, user_id, ROLE_USER)
    await db.commit()
    return {"userId": user_id, "emailVerificationRequired": need_verify}


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    email: str | None,
    real_name: str | None,
    avatar: str | None,
    status: int | None,
    role_codes: list[str] | None,
) -> str:
    if await find_by_username(db, username):
        raise ConflictError("用户名已存在")

    user_id = next_snowflake_id()
    now = utcnow()
    db.add(
        User(
            id=user_id,
            username=username,
            password=hash_password(password),
            email=email,
            real_name=real_name,
            avatar=avatar,
            status=status if status is not None else 1,
            email_verified=1,
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    codes = role_codes if role_codes else [ROLE_USER]
    await replace_roles(db, user_id, codes)
    await db.commit()
    return user_id


async def update_user(db: AsyncSession, user_id: str, dto: dict) -> dict:
    user = await find_by_id_or_throw(db, user_id)
    for field, column in [
        ("email", "email"),
        ("realName", "real_name"),
        ("avatar", "avatar"),
        ("status", "status"),
    ]:
        if dto.get(field) is not None:
            setattr(user, column, dto[field])
    user.updated_at = utcnow()
    await db.commit()
    return await to_user_vo(db, user)


async def delete_user(db: AsyncSession, user_id: str) -> None:
    user = await find_by_id_or_throw(db, user_id)
    user.deleted = True
    user.updated_at = utcnow()
    await db.commit()


async def page_users(db: AsyncSession, query: dict) -> dict:
    page = query.get("page") or 1
    page_size = query.get("pageSize") or 20

    base = select(User).where(User.deleted.is_(False))
    count_q = select(func.count()).select_from(User).where(User.deleted.is_(False))
    keyword = (query.get("keyword") or "").strip()
    if keyword:
        kw = f"%{keyword}%"
        cond = (
            User.username.ilike(kw) | User.real_name.ilike(kw) | User.email.ilike(kw)
        )
        base = base.where(cond)
        count_q = count_q.where(cond)
    if query.get("status") is not None:
        cond = User.status == query["status"]
        base = base.where(cond)
        count_q = count_q.where(cond)
    if query.get("roleCode"):
        role_code = query["roleCode"]
        base = base.where(
            User.id.in_(
                select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(
                    Role.role_code == role_code
                )
            )
        )
        count_q = count_q.where(
            User.id.in_(
                select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(
                    Role.role_code == role_code
                )
            )
        )

    total = (await db.execute(count_q)).scalar_one()
    users = (
        (
            await db.execute(
                base.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    import asyncio

    items = await asyncio.gather(*[to_user_vo(db, u) for u in users])
    return {"items": list(items), "total": total, "page": page, "pageSize": page_size}


async def get_user_vo(db: AsyncSession, user_id: str) -> dict:
    user = await find_by_id_or_throw(db, user_id)
    return await to_user_vo(db, user)


async def get_user_role_codes(db: AsyncSession, user_id: str) -> list[str]:
    await find_by_id_or_throw(db, user_id)
    return await get_role_codes(db, user_id)


async def replace_roles(db: AsyncSession, user_id: str, role_codes: list[str]) -> list[str]:
    await find_by_id_or_throw(db, user_id)
    roles = (
        (
            await db.execute(
                select(Role).where(Role.role_code.in_(role_codes), Role.status == 1)
            )
        )
        .scalars()
        .all()
    )
    if len(roles) != len(set(role_codes)):
        found = {r.role_code for r in roles}
        missing = [c for c in dict.fromkeys(role_codes) if c not in found]
        raise NotFoundError(f"角色不存在: {', '.join(missing)}")

    await db.execute(sa_delete(UserRole).where(UserRole.user_id == user_id))
    for role in roles:
        db.add(UserRole(id=next_snowflake_id(), user_id=user_id, role_id=role.id,
                        created_at=utcnow()))
    await db.flush()
    return role_codes


async def assign_role(db: AsyncSession, user_id: str, role_code: str) -> None:
    role = (
        await db.execute(select(Role).where(Role.role_code == role_code))
    ).scalar_one_or_none()
    if not role:
        raise NotFoundError(f"角色 {role_code} 不存在")
    exists = (
        await db.execute(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id)
        )
    ).scalar_one_or_none()
    if exists:
        return
    db.add(UserRole(id=next_snowflake_id(), user_id=user_id, role_id=role.id,
                    created_at=utcnow()))
    await db.flush()


async def touch_last_login(db: AsyncSession, user_id: str) -> None:
    user = await find_by_id_or_throw(db, user_id)
    user.last_login_at = utcnow()
    await db.commit()


async def get_user_ids_by_role_code(db: AsyncSession, role_code: str) -> list[str]:
    rows = await db.execute(
        select(User.id)
        .join(UserRole, User.id == UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.role_code == role_code, User.deleted.is_(False), User.status == 1)
    )
    return [str(r[0]) for r in rows.all()]


async def list_all_roles(db: AsyncSession) -> list[dict]:
    roles = (
        (await db.execute(select(Role).where(Role.status == 1).order_by(Role.role_name.asc())))
        .scalars()
        .all()
    )
    return [serializers.role_vo(r) for r in roles]


async def update_profile(db: AsyncSession, user_id: str, dto: dict) -> dict:
    user = await find_by_id_or_throw(db, user_id)
    for field, column in [("email", "email"), ("realName", "real_name"), ("avatar", "avatar")]:
        if dto.get(field) is not None:
            setattr(user, column, dto[field])
    user.updated_at = utcnow()
    await db.commit()
    return await to_user_vo(db, user)


async def change_password(db: AsyncSession, user_id: str, old_password: str, new_password: str) -> None:
    user = await find_by_id_or_throw(db, user_id)
    if not verify_password(old_password, user.password):
        raise BadRequestError("原密码错误")
    user.password = hash_password(new_password)
    user.updated_at = utcnow()
    await db.commit()


async def reset_password(db: AsyncSession, user_id: str, new_password: str) -> None:
    user = await find_by_id_or_throw(db, user_id)
    user.password = hash_password(new_password)
    user.updated_at = utcnow()
    await db.commit()


async def reset_password_by_email(db: AsyncSession, email: str, new_password: str) -> None:
    user = await find_by_email(db, email)
    if not user:
        raise NotFoundError("该邮箱未注册")
    user.password = hash_password(new_password)
    user.updated_at = utcnow()
    await db.commit()


async def activate_email(db: AsyncSession, user_id: str) -> str:
    user = await find_by_id_or_throw(db, user_id)
    if user.email_verified == 1:
        return "账户已激活，请直接登录"
    user.email_verified = 1
    user.status = 1
    user.updated_at = utcnow()
    await db.commit()
    return "账户激活成功，请登录"


async def get_user_statistics(db: AsyncSession, user_id: str) -> dict:
    await find_by_id_or_throw(db, user_id)
    document_count = (
        await db.execute(
            select(func.count()).select_from(Document).where(
                Document.author_id == user_id, Document.deleted.is_(False)
            )
        )
    ).scalar_one()
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Document.view_count), 0),
                func.coalesce(func.sum(Document.like_count), 0),
                func.coalesce(func.sum(Document.comment_count), 0),
            ).where(Document.author_id == user_id, Document.deleted.is_(False))
        )
    ).one()
    return {
        "documentCount": document_count,
        "viewCount": int(row[0]),
        "likeCount": int(row[1]),
        "commentCount": int(row[2]),
    }
