"""JWT / bcrypt — 对应 @nestjs/jwt + bcryptjs。

- HS256，同一密钥签发 access / refresh，payload {sub, username, type}
- 过期表达式 "2h"/"7d"/"30s"/"15m" → 秒
- bcrypt rounds=10，与种子数据 $2a$10$ 兼容
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

_DEFAULT_EXPIRES = 7200
_EXPIRES_RE = re.compile(r"^(\d+)([smhd])$")


def parse_expires(value: str | None) -> int:
    """'2h' → 7200；不识别回退 7200（与 TS 版一致）。"""
    if not value:
        return _DEFAULT_EXPIRES
    m = _EXPIRES_RE.match(value.strip())
    if not m:
        return _DEFAULT_EXPIRES
    amount = int(m.group(1))
    unit = m.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return amount * multiplier


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(secret: str, user_id: str, username: str, token_type: str, expires_in: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict | None:
    """签名/过期非法时返回 None（调用方转 401）。"""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
