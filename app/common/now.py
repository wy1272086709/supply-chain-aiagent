"""统一时间源 — PG `TIMESTAMP WITHOUT TIME ZONE` 列必须写 naive datetime（asyncpg 限制）。

全库约定：DB 存 naive UTC；序列化时由 iso_z 补 "Z"。
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
