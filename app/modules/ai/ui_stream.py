"""UIMessageStream SSE 发射器 — 逐字节复刻 Vercel AI SDK 的线上协议。

线上格式（已在 node_modules/ai@7 中验证）：
- 每帧 `data: {JSON}\\n\\n`，JSON.stringify 无空格 → separators=(",",":")
- 结尾 `data: [DONE]\\n\\n`
- 响应头含 x-vercel-ai-ui-message-stream: v1
- writer.write(chunk) 的字段原样透传（data-* 无 id；text-delta 用 delta 字段）
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator


def sse_line(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n"


UI_MESSAGE_STREAM_HEADERS = {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    "connection": "keep-alive",
    "x-accel-buffering": "no",
    "x-vercel-ai-ui-message-stream": "v1",
}


class UiStreamEmitter:
    """把协议帧编码为 SSE 行；内部维护 text/reasoning 部分的开启状态。"""

    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._open_text_id: str | None = None
        self._open_reasoning_id: str | None = None
        self._open_tool_calls: dict[str, dict] = {}
        self._tool_inputs_sent: set[str] = set()

    # ---------------- 基础帧 ----------------

    def write(self, chunk: dict) -> None:
        self._chunks.append(sse_line(chunk))

    def start(self) -> None:
        self.write({"type": "start"})

    def finish(self) -> None:
        self.write({"type": "finish"})

    def error(self, error_text: str) -> None:
        self.write({"type": "error", "errorText": error_text})

    def data(self, name: str, payload) -> None:
        """data-status / data-intent / data-eval / data-sources / data-graph / data-session。"""
        self.write({"type": f"data-{name}", "data": payload})

    def source_document(self, source_id: str, title: str) -> None:
        self.write(
            {
                "type": "source-document",
                "sourceId": source_id,
                "mediaType": "text/markdown",
                "title": title,
            }
        )

    def done(self) -> None:
        self._chunks.append("data: [DONE]\n\n")

    # ---------------- 文本 / 思考生命周期 ----------------

    def text_start(self, part_id: str) -> None:
        self.write({"type": "text-start", "id": part_id})

    def text_delta(self, part_id: str, delta: str) -> None:
        self.write({"type": "text-delta", "id": part_id, "delta": delta})

    def text_end(self, part_id: str) -> None:
        self.write({"type": "text-end", "id": part_id})

    def reasoning_start(self, part_id: str) -> None:
        self.write({"type": "reasoning-start", "id": part_id})

    def reasoning_delta(self, part_id: str, delta: str) -> None:
        self.write({"type": "reasoning-delta", "id": part_id, "delta": delta})

    def reasoning_end(self, part_id: str) -> None:
        self.write({"type": "reasoning-end", "id": part_id})

    # ---------------- 工具帧（与 @ai-sdk/langchain 适配层一致） ----------------

    def tool_input_start(self, tool_call_id: str, tool_name: str) -> None:
        self.write({"type": "tool-input-start", "toolCallId": tool_call_id, "toolName": tool_name})

    def tool_input_available(self, tool_call_id: str, tool_name: str, input_data: dict | str) -> None:
        self.write(
            {
                "type": "tool-input-available",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "input": input_data,
            }
        )

    def tool_output_available(self, tool_call_id: str, output) -> None:
        self.write({"type": "tool-output-available", "toolCallId": tool_call_id, "output": output})

    # ---------------- 状态查询 ----------------

    @property
    def open_text_id(self) -> str | None:
        return self._open_text_id

    @property
    def open_reasoning_id(self) -> str | None:
        return self._open_reasoning_id

    def set_open_text(self, part_id: str | None) -> None:
        self._open_text_id = part_id

    def set_open_reasoning(self, part_id: str | None) -> None:
        self._open_reasoning_id = part_id

    @property
    def tool_inputs_sent(self) -> set[str]:
        return self._tool_inputs_sent

    def drain(self) -> list[str]:
        chunks, self._chunks = self._chunks, []
        return chunks


def new_part_id() -> str:
    return uuid.uuid4().hex[:16]
