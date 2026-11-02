"""TraceLogMiddleware — Thought/Action/Observation 追踪日志测试。"""

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.modules.ai.trace_log import TraceLogMiddleware, set_trace_context


def _ai_msg(content="", tool_calls=None):
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_after_model_logs_thought_and_actions(caplog):
    mw = TraceLogMiddleware()
    state = {
        "messages": [
            HumanMessage("加班餐补怎么报？"),
            _ai_msg("先并行检索知识库与图谱", [
                {"name": "retrieve_knowledge", "args": {"query": "加班餐补"}, "id": "c1"},
                {"name": "retrieve_graph", "args": {"query": "餐补"}, "id": "c2"},
            ]),
        ]
    }
    with caplog.at_level(logging.INFO, logger="app.modules.ai.trace_log"):
        assert mw.after_model(state, None) is None
    text = caplog.text
    assert "Thought: 先并行检索知识库与图谱" in text
    assert "Action: retrieve_knowledge(" in text and "加班餐补" in text
    assert "Action: retrieve_graph(" in text
    assert "step=1" in text
    # 第二次模型调用序号递增
    with caplog.at_level(logging.INFO, logger="app.modules.ai.trace_log"):
        mw.after_model({"messages": [_ai_msg("资料已切题，开始作答")]}, None)
    assert "step=2" in caplog.text
    assert "作答收尾" in caplog.text


def test_after_model_truncates_and_never_raises(caplog):
    mw = TraceLogMiddleware()
    long_thought = "长思考" * 500  # 1000 字
    with caplog.at_level(logging.INFO, logger="app.modules.ai.trace_log"):
        mw.after_model({"messages": [_ai_msg(long_thought)]}, None)
    assert "..." in caplog.text
    # 坏 state 不抛异常（追踪不打断主流程）
    mw.after_model({}, None)  # 缺 messages 键
    mw.after_model(object(), None)  # 非 dict 无 messages 属性


def test_trace_context_in_prefix(caplog):
    set_trace_context("sid=42 uid=7")
    mw = TraceLogMiddleware()
    with caplog.at_level(logging.INFO, logger="app.modules.ai.trace_log"):
        mw.after_model({"messages": [_ai_msg("思考中")]}, None)
    assert "[agent-trace sid=42 uid=7]" in caplog.text
    set_trace_context("-")  # 还原，避免污染其他用例


async def test_awrap_tool_call_logs_observation(caplog):
    from langgraph.prebuilt.tool_node import ToolCallRequest

    mw = TraceLogMiddleware()
    request = ToolCallRequest(
        tool_call={"name": "retrieve_knowledge", "args": {"query": "报销"}, "id": "c1"},
        tool=None,
        state={},
        runtime=None,
    )

    async def handler(req):
        return ToolMessage(content='{"eval": {"ok": true}}', tool_call_id="c1")

    with caplog.at_level(logging.INFO, logger="app.modules.ai.trace_log"):
        result = await mw.awrap_tool_call(request, handler)
    assert result.tool_call_id == "c1"
    assert "Observation: retrieve_knowledge ->" in caplog.text
    assert "ms)" in caplog.text


async def test_awrap_tool_call_logs_and_reraises_error(caplog):
    from langgraph.prebuilt.tool_node import ToolCallRequest

    mw = TraceLogMiddleware()
    request = ToolCallRequest(
        tool_call={"name": "web_search", "args": {"query": "x"}, "id": "c2"},
        tool=None,
        state={},
        runtime=None,
    )

    async def handler(req):
        raise RuntimeError("Bocha 超时")

    with caplog.at_level(logging.WARNING, logger="app.modules.ai.trace_log"):
        with pytest.raises(RuntimeError):
            await mw.awrap_tool_call(request, handler)
    assert "Observation: web_search 抛异常" in caplog.text
