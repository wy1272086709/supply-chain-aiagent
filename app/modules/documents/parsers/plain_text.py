"""TXT / MD — UTF-8 原样读出。"""

from __future__ import annotations


def parse_plain_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")
