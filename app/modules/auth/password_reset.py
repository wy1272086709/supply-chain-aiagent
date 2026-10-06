"""密码重置验证码（Redis，TTL 600s，冷却 60s）— 对应 password-reset.service.ts。"""

from __future__ import annotations

from app.db.redis import get_redis

RESET_CODE_PREFIX = "password:reset:code:"
RESET_CODE_TTL_SECONDS = 10 * 60
RESET_CODE_COOLDOWN_SECONDS = 60


def _key(email: str) -> str:
    return f"{RESET_CODE_PREFIX}{email.lower()}"


async def set_code(email: str, code: str) -> None:
    await get_redis().set(_key(email), code, ex=RESET_CODE_TTL_SECONDS)


async def get_code(email: str) -> str | None:
    return await get_redis().get(_key(email))


async def get_ttl(email: str) -> int:
    ttl = await get_redis().ttl(_key(email))
    return int(ttl) if ttl is not None else -2


async def verify(email: str, code: str) -> bool:
    stored = await get_code(email)
    return stored is not None and stored == code


async def delete(email: str) -> None:
    await get_redis().delete(_key(email))
