"""序列化工具 — ID 转字符串、时间转 JS toISOString 风格（YYYY-MM-DDTHH:MM:SS.mmmZ）。"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

_ISO_RE = re.compile(r"\.\d{6}$")


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(tz=None).replace(tzinfo=None)
    base = value.isoformat(timespec="milliseconds")
    return base + "Z"


def normalize(value):
    """递归把 datetime → ISO-Z 字符串、Decimal → float/str，其余原样。"""
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, datetime):
        return iso_z(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def serialize_row(row) -> dict:
    """SQLAlchemy 模型 → dict（列名驼峰转换由调用方处理，这里只做基础清洗）。"""
    if row is None:
        return None
    if hasattr(row, "__table__"):
        return {c.name: normalize(getattr(row, c.name)) for c in row.__table__.columns}
    return normalize(dict(row))


def snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.title() if not p.isupper() else p for p in parts[1:])
