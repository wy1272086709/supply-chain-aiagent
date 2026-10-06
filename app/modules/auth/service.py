"""认证服务 — 对应 src/auth/auth.service.ts。"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError, UnauthorizedError
from app.core.security import create_token, decode_token, parse_expires
from app.modules.auth import email_activation, password_reset
from app.modules.auth.email_service import send_activation_email, send_reset_code_email
from app.modules.users import service as user_service


def _build_login_result(user: dict) -> dict:
    cfg = get_settings()
    access_expires = parse_expires(cfg.jwt_access_expires)
    refresh_expires = parse_expires(cfg.jwt_refresh_expires)
    return {
        "accessToken": create_token(
            cfg.jwt_secret, user["userId"], user["username"], "access", access_expires
        ),
        "refreshToken": create_token(
            cfg.jwt_secret, user["userId"], user["username"], "refresh", refresh_expires
        ),
        "tokenType": "Bearer",
        "expiresIn": access_expires,
        "userInfo": user,
    }


async def login(db: AsyncSession, dto) -> dict:
    user = await user_service.validate_credentials(db, dto.username, dto.password)
    await user_service.touch_last_login(db, user["userId"])
    return _build_login_result(user)


async def register(db: AsyncSession, dto) -> dict:
    cfg = get_settings()
    result = await user_service.register(
        db,
        username=dto.username,
        password=dto.password,
        email=dto.email,
        real_name=dto.realName,
        require_email_verification=cfg.email_verification_required,
    )
    if result.get("emailVerificationRequired") and dto.email:
        token = await email_activation.create_token(result["userId"])
        try:
            await send_activation_email(dto.email, dto.username, token)
        except Exception:
            await email_activation.delete_by_token(token)
            raise BadRequestError("激活邮件发送失败，请稍后再试")
        return {
            "userId": result["userId"],
            "message": "注册成功，请查收邮件激活账户",
            "emailVerificationRequired": True,
        }
    return {"userId": result["userId"], "message": "注册成功，请登录"}


async def verify_email(db: AsyncSession, token: str) -> dict:
    user_id = await email_activation.consume_token(token)
    if not user_id:
        raise BadRequestError("激活链接无效或已过期")
    message = await user_service.activate_email(db, user_id)
    return {"message": message}


async def send_reset_code(db: AsyncSession, dto) -> dict:
    user = await user_service.find_by_email(db, dto.email)
    if not user:
        raise NotFoundError("该邮箱未注册")

    ttl = await password_reset.get_ttl(dto.email)
    if ttl > password_reset.RESET_CODE_TTL_SECONDS - password_reset.RESET_CODE_COOLDOWN_SECONDS:
        raise BadRequestError("验证码已发送，请稍后再试")

    code = str(random.randint(100000, 999999))
    await password_reset.set_code(dto.email, code)
    try:
        await send_reset_code_email(dto.email, user.username, code)
    except Exception:
        await password_reset.delete(dto.email)
        raise BadRequestError("邮件发送失败，请稍后再试")
    return {"message": "验证码已发送"}


async def reset_password_by_email(db: AsyncSession, dto) -> dict:
    if not await password_reset.verify(dto.email, dto.code):
        raise BadRequestError("验证码错误或已过期")
    await user_service.reset_password_by_email(db, dto.email, dto.newPassword)
    await password_reset.delete(dto.email)
    return {"message": "密码重置成功，请登录"}


async def refresh(db: AsyncSession, refresh_token: str) -> dict:
    cfg = get_settings()
    payload = decode_token(cfg.jwt_secret, refresh_token)
    if not payload:
        raise UnauthorizedError("refresh token 无效或已过期")
    if payload.get("type") != "refresh":
        raise UnauthorizedError("无效的 refresh token")
    user = await user_service.build_auth_user(db, str(payload.get("sub")))
    return _build_login_result(user)


async def get_me(db: AsyncSession, user_id: str) -> dict:
    return await user_service.build_auth_user(db, user_id)


async def get_reviewer_ids(db: AsyncSession) -> list[str]:
    return await user_service.get_user_ids_by_role_code(db, "ROLE_REVIEWER")
