"""Agent 循环追踪日志 — Thought / Action / Observation 三件套全打出来。

排查死循环/卡点的第一入口：拿到日志就能复述模型每一步"想了什么、做了什么、
看到什么"。实现为 AgentMiddleware（与 Summarization / ModelCallLimit 同机制）：

- after_model：模型回合结束 → Thought（AI 正文）+ Action（每个 tool_call 的名称与入参）
- awrap_tool_call：包住工具执行 → Observation（结果摘要 + 耗时），异常也打

三条铁律：截断防刷屏；追踪自身异常绝不打断主流程；step 计数是实例属性，
实例随 create_turn_agent 每 turn 新建 → 序号天然按轮归零（与 retrieve_count 闭包同套路）。
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

_trace_ctx: ContextVar[str] = ContextVar("agent_trace_ctx", default="-")

THOUGHT_LEN = 200
PAYLOAD_LEN = 300


def set_trace_context(label: str) -> None:
    """把 sessionId/userId 绑进当前协程上下文，中间件日志据此关联请求。

    contextvars 随协程传播：async 生成器里并发多个请求也不会串线。
    """
    _trace_ctx.set(label)


def _short(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


class TraceLogMiddleware(AgentMiddleware):
    """Agent 循环 Thought/Action/Observation 追踪日志。"""

    def __init__(self) -> None:
        super().__init__()
        self.step = 0  # 模型调用序号，与 run_limit 对齐（第 8 次被收口一眼看出）

    def _tag(self) -> str:
        return f"[agent-trace {_trace_ctx.get()}] step={self.step}"

    def after_model(self, state, runtime) -> dict | None:  # noqa: ANN001
        """模型回合结束：打 Thought + Action。"""
        try:
            messages = state["messages"] if isinstance(state, dict) else getattr(state, "messages", [])
            last = messages[-1] if messages else None
            if not isinstance(last, AIMessage):
                return None
            self.step += 1
            tag = self._tag()
            content = last.content
            thought = content.strip() if isinstance(content, str) else ""
            if thought:
                logger.info("%s Thought: %s", tag, _short(thought, THOUGHT_LEN))
            tool_calls = getattr(last, "tool_calls", None) or []
            for tc in tool_calls:
                args = json.dumps(tc.get("args") or {}, ensure_ascii=False)
                logger.info("%s Action: %s(%s)", tag, tc.get("name"), _short(args, PAYLOAD_LEN))
            if not tool_calls:
                logger.info("%s Thought: （本轮无工具调用，作答收尾）", tag)
        except Exception:  # noqa: BLE001 — 追踪日志绝不打断主流程
            logger.debug("agent-trace after_model 记录失败", exc_info=True)
        return None

    async def awrap_tool_call(self, request, handler) -> Any:  # noqa: ANN001
        """包住工具执行：打 Observation + 耗时；工具抛异常也打完再原样上抛。"""
        call = request.tool_call or {}
        name = call.get("name") or "?"
        tag = self._tag()
        start = time.monotonic()
        try:
            result = await handler(request)
            content = getattr(result, "content", result)
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            logger.info(
                "%s Observation: %s -> %s (%dms)",
                tag, name, _short(content, PAYLOAD_LEN), int((time.monotonic() - start) * 1000),
            )
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "%s Observation: %s 抛异常 %s (%dms)",
                tag, name, e, int((time.monotonic() - start) * 1000),
            )
            raise
