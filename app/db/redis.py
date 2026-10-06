"""Redis 客户端 — 对应 src/redis/redis.service.ts 的 get/set/del/ttl。"""

from __future__ import annotations

from redis import asyncio as aioredis

from app.core.config import get_settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        cfg = get_settings()
        _client = aioredis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password or None,
            db=cfg.redis_db,
            decode_responses=True,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.quit()
        _client = None
