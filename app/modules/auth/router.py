"""Auth 路由 — 对应 src/auth/auth.controller.ts。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.common.constants import ROLE_ADMIN, ROLE_REVIEWER
from app.modules.auth import service
from app.modules.auth.deps import CurrentUser, DbSession, require_roles
from app.modules.auth.schemas import (
    LoginDto,
    RefreshTokenDto,
    RegisterDto,
    ResetPasswordByEmailDto,
    SendResetCodeDto,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
async def register(dto: RegisterDto, db: DbSession):
    return await service.register(db, dto)


@router.post("/login")
async def login(dto: LoginDto, db: DbSession):
    return await service.login(db, dto)


@router.post("/refresh")
async def refresh(dto: RefreshTokenDto, db: DbSession):
    return await service.refresh(db, dto.refreshToken)


@router.get("/verify-email")
async def verify_email(token: str, db: DbSession):
    return await service.verify_email(db, token)


@router.post("/password/reset/send-code")
async def send_reset_code(dto: SendResetCodeDto, db: DbSession):
    return await service.send_reset_code(db, dto)


@router.post("/password/reset")
async def reset_password(dto: ResetPasswordByEmailDto, db: DbSession):
    return await service.reset_password_by_email(db, dto)


@router.post("/logout")
async def logout():
    """无状态 JWT：客户端丢弃 token 即可。"""
    return {"message": "已退出登录"}


@router.get("/me")
async def me(user: CurrentUser, db: DbSession):
    return await service.get_me(db, user["userId"])


@router.get("/reviewer-ids")
async def reviewer_ids(
    db: DbSession, user=Depends(require_roles(ROLE_ADMIN, ROLE_REVIEWER))
):
    return await service.get_reviewer_ids(db)
