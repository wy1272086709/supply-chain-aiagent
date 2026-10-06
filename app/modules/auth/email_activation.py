"""邮箱激活令牌（Redis，24h）— 对应 email-activation.service.ts。"""

from __future__ import annotations

import secrets

from app.db.redis import get_redis

TOKEN_PREFIX = "email:activation:token:"
USER_PREFIX = "email:activation:user:"
ACTIVATION_TOKEN_TTL_SECONDS = 24 * 3600


def _token_key(token: str) -> str:
    return f"{TOKEN_PREFIX}{token}"


def _user_key(user_id: str) -> str:
    return f"{USER_PREFIX}{user_id}"


async def create_token(user_id: str) -> str:
    redis = get_redis()
    existing = await redis.get(_user_key(user_id))
    if existing:
        await redis.delete(_token_key(existing))

    token = secrets.token_hex(32)
    # token → userId：点激活链接时反查账号；userId → token：重发时作废旧 token
    await redis.set(_token_key(token), user_id, ex=ACTIVATION_TOKEN_TTL_SECONDS)
    await redis.set(_user_key(user_id), token, ex=ACTIVATION_TOKEN_TTL_SECONDS)
    return token


async def consume_token(token: str) -> str | None:
    redis = get_redis()
    user_id = await redis.get(_token_key(token))
    if not user_id:
        return None
    await redis.delete(_token_key(token))
    await redis.delete(_user_key(user_id))
    return user_id


async def delete_by_token(token: str) -> None:
    redis = get_redis()
    user_id = await redis.get(_token_key(token))
    await redis.delete(_token_key(token))
    if user_id:
        await redis.delete(_user_key(user_id))
