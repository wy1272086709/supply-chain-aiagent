"""对话短期记忆：Redis 热窗口 — 对应 chat-short-memory.service.ts。

存 LangChain 消息序列化格式（type+content 的简明形式，自持读写）。
只存 human/ai 原文；miss/故障由调用方从 Postgres 回填。
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.core.config import get_settings
from app.db.redis import get_redis
from app.modules.ai.memory_util import is_working_message

logger = logging.getLogger(__name__)


def window_size() -> int:
    return get_settings().chat_short_memory_max_messages


def _key(user_id: str, session_id: str) -> str:
    cfg = get_settings()
    return f"{cfg.chat_short_memory_key_prefix}:{user_id}:{session_id}:messages"


def _serialize(messages: list[BaseMessage]) -> str:
    payload = []
    for m in messages:
        if isinstance(m, HumanMessage):
            payload.append({"type": "human", "content": m.content})
        elif isinstance(m, AIMessage):
            payload.append({"type": "ai", "content": m.content})
    return json.dumps(payload, ensure_ascii=False)


def _deserialize(raw: str) -> list[BaseMessage]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []
    out: list[BaseMessage] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "human":
            out.append(HumanMessage(item.get("content") or ""))
        elif item.get("type") == "ai":
            out.append(AIMessage(item.get("content") or ""))
    return out


async def try_load(user_id: str, session_id: str) -> list[BaseMessage] | None:
    """命中返回消息；key 不存在或 Redis 故障返回 None。"""
    try:
        raw = await get_redis().get(_key(user_id, session_id))
        if not raw:
            return None
        return [m for m in _deserialize(raw) if is_working_message(m)]
    except Exception as e:  # noqa: BLE001
        logger.warning("短期记忆 Redis 读取失败，改走数据库：%s", e)
        return None


async def save(user_id: str, session_id: str, messages: list[BaseMessage]) -> None:
    cfg = get_settings()
    working = [m for m in messages if is_working_message(m)][-cfg.chat_short_memory_max_messages :]
    try:
        await get_redis().set(
            _key(user_id, session_id), _serialize(working), ex=cfg.chat_short_memory_ttl_seconds
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("短期记忆 Redis 写入失败：%s", e)


async def append_turn(
    user_id: str, session_id: str, history: list[BaseMessage], question: str, answer: str
) -> None:
    await save(user_id, session_id, [*history, HumanMessage(question), AIMessage(answer)])


async def clear(user_id: str, session_id: str) -> None:
    try:
        await get_redis().delete(_key(user_id, session_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("短期记忆 Redis 删除失败：%s", e)
