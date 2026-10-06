"""鉴权依赖链 — 对应 JwtAuthGuard → RolesGuard → PermissionsGuard 三个全局守卫。

用法：
- 公开路由：不声明 get_current_user 依赖（等价 @Public）
- @Roles(...)：Depends(require_roles(...))
- @RequirePermission(...)：Depends(require_permissions(...))
- @CurrentUser：get_current_user 的返回值（每请求从 DB 重建 AuthUser）
"""

from __future__ import annotations

from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.constants import ROLE_ADMIN
from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.postgres import get_db
from app.modules.users import service as user_service

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(request: Request, db: DbSession) -> dict:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise UnauthorizedError("未登录或 token 已失效")
    token = auth[7:].strip()
    cfg = get_settings()
    try:
        payload = pyjwt.decode(token, cfg.jwt_secret, algorithms=["HS256"])
    except pyjwt.PyJWTError:
        raise UnauthorizedError("未登录或 token 已失效")
    if payload.get("type") != "access":
        raise UnauthorizedError("未登录或 token 已失效")
    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise UnauthorizedError("未登录或 token 已失效")
    try:
        return await user_service.build_auth_user(db, user_id)
    except UnauthorizedError:
        raise
    except Exception:
        raise UnauthorizedError("未登录或 token 已失效")


CurrentUser = Annotated[dict, Depends(get_current_user)]


def require_roles(*role_codes: str):
    """任一角色命中即通过（对应 RolesGuard 的 any-of）。"""

    async def dependency(user: CurrentUser) -> dict:
        if not any(r in user["roles"] for r in role_codes):
            raise ForbiddenError("权限不足")
        return user

    return dependency


def require_permissions(*permission_codes: str):
    """无要求直通；ROLE_ADMIN 直通；任一权限命中通过（对应 PermissionsGuard）。"""

    async def dependency(user: CurrentUser) -> dict:
        if not permission_codes:
            return user
        if ROLE_ADMIN in user["roles"]:
            return user
        if not any(p in user["permissions"] for p in permission_codes):
            raise ForbiddenError("权限不足")
        return user

    return dependency


def require_roles_or_permissions(*role_codes: str, permissions: tuple[str, ...] = ()):
    """角色 AND 权限（user.controller 的组合模式），permissions 传 (code,) 表示任一。"""

    async def dependency(user: CurrentUser) -> dict:
        if role_codes and not any(r in user["roles"] for r in role_codes):
            raise ForbiddenError("权限不足")
        if permissions:
            if ROLE_ADMIN in user["roles"]:
                return user
            if not any(p in user["permissions"] for p in permissions):
                raise ForbiddenError("权限不足")
        return user

    return dependency
