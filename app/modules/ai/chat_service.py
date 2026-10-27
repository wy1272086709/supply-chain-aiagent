"""非流式 Agentic RAG — 对应 ai-chat.service.ts。

意图路由 → 知识库与图谱并行检索（不足则改写再查）→ 按需联网 → 作答。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError
from app.db.postgres import get_session_factory
from app.modules.ai import long_memory, query_rewrite, sessions, short_memory, web_search
from app.modules.ai.agentic_retrieve import retrieve_until_relevant
from app.modules.ai.memory_util import db_rows_to_messages
from app.modules.documents.access import access_from_user
from app.modules.pipeline.graph_build import format_graph_system_text, retrieve_for_chat

logger = logging.getLogger(__name__)

EXCERPT_LEN = 200
CITATION_RE = re.compile(r"\[(\d+)\]")

_llm = None
_tried = False


def _get_llm():
    global _llm, _tried
    if _tried:
        return _llm
    _tried = True
    cfg = get_settings()
    if not cfg.llm_api_key_resolved:
        return None
    from langchain_openai import ChatOpenAI

    _llm = ChatOpenAI(
        api_key=cfg.llm_api_key_resolved,
        model=cfg.llm_model_resolved,
        temperature=0.2,
        timeout=cfg.ai_chat_timeout_ms,
        max_retries=0,
        base_url=cfg.llm_base_url_resolved,
    )
    return _llm


async def load_working_history(user_id: str, session_id: str | None) -> list[BaseMessage]:
    """Redis 热窗口 miss 时从 PG 回填再写回。"""
    if not session_id:
        return []
    cached = await short_memory.try_load(user_id, session_id)
    if cached:
        return cached
    factory = get_session_factory()
    async with factory() as db:
        rows = await sessions.list_recent_messages(db, user_id, session_id, short_memory.window_size())
    history = db_rows_to_messages(rows)
    if history:
        await short_memory.save(user_id, session_id, history)
    return history


def _excerpt(content: str) -> str:
    text = re.sub(r"\s+", " ", content or "").strip()
    return text if len(text) <= EXCERPT_LEN else text[:EXCERPT_LEN] + "..."


def _to_cited_sources(answer: str, hits: list[dict]) -> list[dict]:
    """从回答抽出 [n]；未标注回退为全部召回（摘录）。"""
    cited: dict[int, None] = {}
    for m in CITATION_RE.finditer(answer or ""):
        n = int(m.group(1))
        if 1 <= n <= len(hits):
            cited.setdefault(n, None)
    indexes = sorted(cited.keys()) if cited else list(range(1, len(hits) + 1))
    return [_to_source(index, hits[index - 1]) for index in indexes]


def _to_source(index: int, hit: dict) -> dict:
    return {
        "index": index,
        "documentId": hit["documentId"],
        "documentTitle": hit["documentTitle"],
        "heading": hit.get("heading"),
        "excerpt": _excerpt(hit.get("content") or ""),
        "score": hit.get("score", 0),
    }


def _build_context(hits: list[dict]) -> str:
    blocks = []
    for i, src in enumerate(hits):
        heading = f" / {src['heading']}" if src.get("heading") else ""
        content = src.get("content") or ""
        snippet = content[:800] + "..." if len(content) > 800 else content
        blocks.append(f"[{i + 1}] {src['documentTitle']}{heading}\n{snippet}")
    return "\n\n".join(blocks)


def _build_web_context(web: dict) -> str:
    if web.get("error"):
        return web["error"]
    if not web.get("items"):
        return "无结果。"
    return "\n\n".join(
        f"{i + 1}. {hit['title']}\n{hit['url']}\n{hit['snippet']}"
        for i, hit in enumerate(web["items"])
    )


def _build_system_prompt(intent: str, has_kb: bool, web: dict | None) -> str:
    prompt = (
        "你是企业知识库助手。结合对话历史和记忆里的用户背景回答。"
        "制度/流程以本轮知识库资料为准，不要用记忆替代文档。"
    )
    if has_kb:
        prompt += (
            "凡是依据某条资料作出的陈述，必须在句末标注对应编号，如 [1]、[2]。"
            "编号必须与资料列表一致，不要标注未使用的编号，不要编造文档标题或链接。"
        )
    elif intent in ("kb", "kb_then_web"):
        prompt += "知识库没有切题资料，不要编造内部制度。"
    if web and web.get("items"):
        prompt += "联网结果只作公开信息补充，用标题+链接说明，不要写成公司内部规定。"
    if intent in ("chitchat", "profile"):
        prompt += "可回应寒暄或个人偏好，不要编造制度。"
    prompt += "若资料不足以回答，明确说不知道。回答简洁，必要时列出条目。"
    return prompt


async def chat(question: str, top_k: int = 5, user: dict | None = None, session_id: str | None = None) -> dict:
    trimmed = (question or "").strip()
    if not trimmed:
        return {"sessionId": session_id or None, "answer": "请输入问题。", "sources": []}

    history = await load_working_history(user["userId"], session_id) if user else []
    plan = await query_rewrite.classify(trimmed, history)

    mem_hits_task = (
        long_memory.search(user["userId"], session_id, plan["query"])
        if user
        else _no_memory()
    )
    # 图谱与知识库同一层：意图确定后再并行检索
    access = access_from_user(user) if user else None
    kb_task = (
        retrieve_until_relevant(question=trimmed, query=plan["query"], top_k=top_k, user=user)
        if plan["allowRetrieve"]
        else _no_retrieval()
    )
    graph_task = (
        retrieve_for_chat(plan["graphQueries"], 8, access)
        if plan["allowGraph"] and user and access and plan["graphQueries"]
        else _no_graph()
    )
    retrieved, graph_hit = await asyncio.gather(kb_task, graph_task)

    hits: list[dict] = []
    kb_insufficient = False
    search_query = plan["query"] or trimmed
    if retrieved:
        hits = retrieved["hits"]
        kb_insufficient = not retrieved["eval"]["ok"]
        search_query = retrieved["usedQuery"] or search_query
    has_graph = bool(graph_hit and (graph_hit["entities"] or graph_hit["relations"]))
    web = (
        await web_search.search(search_query)
        if plan["allowWeb"] and (not plan["allowRetrieve"] or kb_insufficient)
        else None
    )
    mem_hits = await mem_hits_task

    if plan["intent"] == "kb" and kb_insufficient and not has_graph:
        # 闸门误杀兜底：判"不切题"但召回非空时，带原召回继续作答。
        # 生成 prompt 只允许依据给定资料作答，资料确实无法支撑时模型会自行说明，避免 rank=1 的正确召回被整批丢弃。
        raw_hits = (retrieved or {}).get("rawHits") or []
        if raw_hits:
            logger.warning(
                "检索闸门判不切题但有 %d 条召回，回退原召回作答：%s",
                len(raw_hits),
                trimmed[:40],
            )
            hits = raw_hits
            kb_insufficient = False
        else:
            empty_answer = "知识库里没有相关内容。"
            session = None
            if user:
                factory = get_session_factory()
                async with factory() as db:
                    session = await sessions.append_turn(
                        db, user["userId"], session_id, trimmed, empty_answer, []
                    )
                await short_memory.append_turn(user["userId"], session.id, history, trimmed, empty_answer)
                asyncio.create_task(long_memory.remember_turn(user["userId"], session.id, trimmed, empty_answer))
            return {"sessionId": (session.id if session else session_id) or None,
                    "answer": empty_answer, "sources": []}

    llm = _get_llm()
    if llm is None:
        raise ServiceUnavailableError("未配置 OPENAI_API_KEY / LLM_API_KEY / DASHSCOPE_API_KEY，无法生成回答")

    memory_msg = long_memory.build_system_message(mem_hits)
    graph_msg = format_graph_system_text(graph_hit) if graph_hit else ""
    parts: list[str] = []
    if hits:
        parts.append(f"知识库资料：\n{_build_context(hits)}")
    if web:
        parts.append(f"联网结果：\n{_build_web_context(web)}")
    parts.append(f"用户问题：{trimmed}")
    user_turn = "\n\n".join(parts)

    messages: list[BaseMessage] = [SystemMessage(_build_system_prompt(plan["intent"], bool(hits), web))]
    if memory_msg:
        messages.append(SystemMessage(memory_msg))
    if graph_msg:
        messages.append(SystemMessage(graph_msg))
    messages.extend(history)
    messages.append(HumanMessage(user_turn))

    response = await llm.ainvoke(messages)
    answer = (
        response.content
        if isinstance(response.content, str)
        else json.dumps(response.content, ensure_ascii=False)
    )

    sources = _to_cited_sources(answer, hits)
    logger.info("RAG 对话完成：hits=%d, cited=%d, answerLength=%d", len(hits), len(sources), len(answer))

    session = None
    if user:
        factory = get_session_factory()
        async with factory() as db:
            session = await sessions.append_turn(db, user["userId"], session_id, trimmed, answer, sources)
        await short_memory.append_turn(user["userId"], session.id, history, trimmed, answer)
        asyncio.create_task(long_memory.remember_turn(user["userId"], session.id, trimmed, answer))

    return {"sessionId": (session.id if session else session_id) or None, "answer": answer, "sources": sources}


async def _no_memory() -> dict:
    return {"user": [], "session": []}


async def _no_retrieval():
    return None


async def _no_graph():
    return None
