"""消息工具 — 对应 chat-memory.util.ts。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and "text" in part:
            parts.append(str(part.get("text") or ""))
    return "".join(parts).strip()


def is_working_message(message: BaseMessage) -> bool:
    return isinstance(message, (HumanMessage, AIMessage))


def db_rows_to_messages(rows: list) -> list[BaseMessage]:
    """PG 行 → LangChain 消息（只认 user/assistant 非空文本）。"""
    out: list[BaseMessage] = []
    for row in rows:
        text = (row.content or "").strip() if hasattr(row, "content") else ""
        if not text:
            continue
        if row.role == "user":
            out.append(HumanMessage(text))
        elif row.role == "assistant":
            out.append(AIMessage(text))
    return out


def compact_rewrite_context(history: list[BaseMessage]) -> str:
    """给检索改写器用：最近 4 条，助手只留短摘要，避免制度原文污染 query。"""
    if not history:
        return ""
    lines: list[str] = []
    for message in history[-4:]:
        text = message_text(message)
        if not text:
            continue
        if isinstance(message, HumanMessage):
            lines.append(f"用户：{text[:200]}")
        elif isinstance(message, AIMessage):
            lines.append(f"助手：{text[:120]}")
    return "\n".join(lines)
