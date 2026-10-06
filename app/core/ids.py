"""BIGINT 列读写策略 — 读出转 str、写入接受 str|int（对应 bigint.transformer.ts）。"""

from __future__ import annotations

from sqlalchemy import BigInteger, TypeDecorator


class StrBigInt(TypeDecorator):
    impl = BigInteger
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return None
        return int(value)
