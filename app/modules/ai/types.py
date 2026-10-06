"""AI 模块共享类型与工具。"""

from __future__ import annotations

import re


def title_from_question(question: str) -> str:
    text = re.sub(r"\s+", " ", (question or "")).strip()
    if not text:
        return "新对话"
    return text[:30] + "…" if len(text) > 30 else text


def excerpt(text: str, limit: int = 200) -> str:
    collapsed = re.sub(r"\s+", " ", (text or "")).strip()
    return collapsed[:limit]


def parse_cited_indexes(answer: str) -> list[int]:
    """从回答中解析 [n] 引用编号（去重、按出现顺序）。"""
    seen: dict[int, None] = {}
    for m in re.finditer(r"\[(\d+)\]", answer or ""):
        seen.setdefault(int(m.group(1)), None)
    return list(seen.keys())
