"""流式 Agentic RAG 编排 — 对应 ai-stream.service.ts。

逐帧复刻 Vercel AI SDK UI Message Stream 协议（发射器见 ui_stream.py）：
start → data-session → data-status(intent) → data-intent →
[tool-input-start/tool-input-available/tool-output-available + data-eval/data-graph/data-sources +
reasoning-* + text-start/delta/end 交错] → finish → [DONE]。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator

from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, SystemMessage

from app.modules.ai import agent as agent_mod
from app.modules.ai import long_memory, query_rewrite, sessions, short_memory
from app.modules.ai.chat_service import load_working_history
from app.modules.ai.types import title_from_question
from app.modules.ai.ui_stream import UiStreamEmitter, new_part_id
from app.db.postgres import get_session_factory

logger = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\[(\d+)\]")


def last_user_text(messages) -> str:
    if not messages:
        return ""
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        parts = msg.parts or []
        return "".join(p.text for p in parts if p.type == "text" and p.text).strip()
    return ""


def _chunk_text(chunk: AIMessageChunk) -> str:
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", None):
                out.append(str(part.get("text") or ""))
        return "".join(out)
    return ""


def _chunk_reasoning(chunk: AIMessageChunk) -> str:
    """DashScope 兼容口把思考增量放 additional_kwargs.reasoning_content。"""
    value = (chunk.additional_kwargs or {}).get("reasoning_content")
    return value if isinstance(value, str) else ""


class _TurnTracker:
    """跟踪单个模型回合内的 text / reasoning / tool-call 部分状态。"""

    def __init__(self, emitter: UiStreamEmitter) -> None:
        self.emitter = emitter
        self.text_id: str | None = None
        self.reasoning_id: str | None = None
        self.tool_calls: dict[str, dict] = {}  # call_id → {name, args_str}
        self.tool_started: set[str] = set()
        self.answer_parts: list[str] = []

    def close_text(self) -> None:
        if self.text_id:
            self.emitter.text_end(self.text_id)
            self.text_id = None

    def close_reasoning(self) -> None:
        if self.reasoning_id:
            self.emitter.reasoning_end(self.reasoning_id)
            self.reasoning_id = None

    def on_chunk(self, chunk: AIMessageChunk) -> None:
        reasoning = _chunk_reasoning(chunk)
        if reasoning:
            if not self.reasoning_id:
                self.reasoning_id = new_part_id()
                self.emitter.reasoning_start(self.reasoning_id)
            self.emitter.reasoning_delta(self.reasoning_id, reasoning)

        text = _chunk_text(chunk)
        if text:
            if not self.text_id:
                # 有正文输出时先收掉思考段（适配层语义：text-start 前发 reasoning-end）
                self.close_reasoning()
                self.text_id = new_part_id()
                self.emitter.text_start(self.text_id)
            self.emitter.text_delta(self.text_id, text)
            self.answer_parts.append(text)

        for tc in getattr(chunk, "tool_call_chunks", None) or []:
            call_id = getattr(tc, "id", None) or ""
            if not call_id:
                continue
            entry = self.tool_calls.setdefault(call_id, {"name": "", "args": ""})
            name = getattr(tc, "name", None)
            if name:
                entry["name"] = name
                if call_id not in self.tool_started:
                    self.tool_started.add(call_id)
                    self.emitter.tool_input_start(call_id, name)
            args = getattr(tc, "args", None)
            if args:
                entry["args"] += args

    def finish_turn(self) -> None:
        """回合结束：收口 reasoning/text，补发已完整的 tool-input-available。"""
        self.close_reasoning()
        self.close_text()
        for call_id, entry in self.tool_calls.items():
            if call_id in self.emitter.tool_inputs_sent:
                continue
            try:
                input_data = json.loads(entry["args"]) if entry["args"] else {}
            except json.JSONDecodeError:
                input_data = {"raw": entry["args"]}
            self.emitter.tool_input_available(call_id, entry["name"] or "", input_data)
            self.emitter.tool_inputs_sent.add(call_id)

    @property
    def answer(self) -> str:
        return "".join(self.answer_parts).strip()


async def stream_chat(dto, user: dict) -> AsyncIterator[str]:
    """SSE 生成器：逐帧 yield `data: ...\n\n` 行，随时 flush 保证流式。"""
    emitter = UiStreamEmitter()

    def flush():
        return emitter.drain()

    emitter.start()
    for line in flush():
        yield line

    question = last_user_text(dto.messages)
    top_k = dto.topK if dto.topK is not None else 5
    persist_session_id = dto.sessionId
    persist_sources: list[dict] = []
    persist_history: list[BaseMessage] = []

    try:
        if not question:
            emitter.text_start("empty")
            emitter.text_delta("empty", "请输入问题。")
            emitter.text_end("empty")
            emitter.finish()
            emitter.done()
            for line in flush():
                yield line
            return

        # ---- 会话：已有 touchTitle，否则按首问新建 ----
        factory = get_session_factory()
        async with factory() as db:
            if dto.sessionId:
                await sessions.touch_title(db, user["userId"], dto.sessionId, question)
                session_id = dto.sessionId
            else:
                session = await sessions.create(db, user["userId"], title_from_question(question))
                session_id = session.id
        persist_session_id = session_id
        emitter.data("session", {"sessionId": session_id})
        for line in flush():
            yield line

        persist_history = await load_working_history(user["userId"], session_id)

        emitter.data("status", {"stage": "intent", "text": "正在识别意图…"})
        for line in flush():
            yield line
        mem_hits_task = asyncio.create_task(long_memory.search(user["userId"], session_id, question))
        plan = await query_rewrite.classify(question, persist_history)
        emitter.data(
            "intent",
            {
                "intent": plan["intent"],
                "label": plan["label"],
                "query": plan["query"],
                "graphQueries": plan["graphQueries"],
                "allowRetrieve": plan["allowRetrieve"],
                "allowGraph": plan["allowGraph"],
                "allowWeb": plan["allowWeb"],
            },
        )
        for line in flush():
            yield line
        mem_hits = await mem_hits_task

        agent = agent_mod.create_turn_agent(user, top_k, persist_sources, emitter, plan, question)
        if agent is None:
            emitter.error("未配置 LLM Key，无法生成回答")
            emitter.finish()
            emitter.done()
            for line in flush():
                yield line
            return

        memory_msg = long_memory.build_system_message(mem_hits)
        messages: list[BaseMessage] = []
        if memory_msg:
            messages.append(SystemMessage(memory_msg))
        messages.extend(persist_history)
        messages.append(HumanMessage(question))

        tracker = _TurnTracker(emitter)
        async for mode, event in agent.astream(
            {"messages": messages}, stream_mode=["messages", "tools"]
        ):
            if mode == "messages":
                chunk, _metadata = event
                if isinstance(chunk, AIMessageChunk):
                    tracker.on_chunk(chunk)
            elif mode == "tools":
                tool_msg = event
                # 工具消息到达 = 本轮模型回合结束：收口 text/reasoning/tool-input
                tracker.finish_turn()
                tool_call_id = getattr(tool_msg, "tool_call_id", None) or ""
                output = getattr(tool_msg, "content", "")
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                emitter.tool_output_available(tool_call_id, output)
            for line in flush():
                yield line
        tracker.finish_turn()
        for line in flush():
            yield line

        emitter.finish()
        emitter.done()
        for line in flush():
            yield line

        # ---- onFinish：落库 + 记忆（流已发完，失败只记日志） ----
        answer = tracker.answer
        used = {int(m.group(1)) for m in CITATION_RE.finditer(answer)}
        sources = [s for s in persist_sources if s["index"] in used] if used else []
        final_answer = answer or "未能生成回答。"
        try:
            async with factory() as db:
                await sessions.append_turn(
                    db, user["userId"], persist_session_id, question, final_answer, sources
                )
            await short_memory.append_turn(
                user["userId"], persist_session_id, persist_history, question, final_answer
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("流式对话落库失败：%s", e)
        try:
            await long_memory.remember_turn(user["userId"], persist_session_id, question, final_answer)
        except Exception as e:  # noqa: BLE001
            logger.warning("长期记忆写入失败：%s", e)

    except Exception as e:  # noqa: BLE001
        logger.warning("LangChain 流失败：%s", e)
        emitter.error(str(e) or "生成回答失败")
        emitter.finish()
        emitter.done()
        for line in emitter.drain():
            yield line
