"""SSE 帧序列 conformance — 验证 ui_stream 发射器产出协议要求的确切字节。"""

import json

from app.modules.ai.ui_stream import UiStreamEmitter, sse_line


def parse_frame(line: str) -> dict:
    assert line.startswith("data: ") and line.endswith("\n\n")
    return json.loads(line[len("data: "):-2])


def test_sse_line_compact_json():
    # JSON.stringify 无空格 → separators=(",",":")
    assert sse_line({"type": "start"}) == 'data: {"type":"start"}\n\n'
    assert sse_line({"type": "text-delta", "id": "t", "delta": "你好"}) == 'data: {"type":"text-delta","id":"t","delta":"你好"}\n\n'


def test_data_frames_passthrough_without_id():
    e = UiStreamEmitter()
    e.data("status", {"stage": "intent", "text": "正在识别意图…"})
    frame = parse_frame(e.drain()[0])
    assert frame == {"type": "data-status", "data": {"stage": "intent", "text": "正在识别意图…"}}
    assert "id" not in frame


def test_text_lifecycle_uses_delta_field():
    e = UiStreamEmitter()
    e.text_start("t1")
    e.text_delta("t1", "hello")
    e.text_delta("t1", " world")
    e.text_end("t1")
    frames = [parse_frame(l) for l in e.drain()]
    assert frames[0] == {"type": "text-start", "id": "t1"}
    assert frames[1] == {"type": "text-delta", "id": "t1", "delta": "hello"}
    assert frames[3] == {"type": "text-end", "id": "t1"}


def test_reasoning_lifecycle():
    e = UiStreamEmitter()
    e.reasoning_start("r1")
    e.reasoning_delta("r1", "思考中")
    e.reasoning_end("r1")
    frames = [parse_frame(l) for l in e.drain()]
    assert [f["type"] for f in frames] == ["reasoning-start", "reasoning-delta", "reasoning-end"]
    assert frames[1]["delta"] == "思考中"


def test_error_and_finish_then_done():
    e = UiStreamEmitter()
    e.start()
    e.error("boom")
    e.finish()
    e.done()
    lines = e.drain()
    frames = [parse_frame(l) for l in lines[:3]]
    assert frames[0]["type"] == "start"
    assert frames[1] == {"type": "error", "errorText": "boom"}
    assert frames[2] == {"type": "finish"}
    assert lines[-1] == "data: [DONE]\n\n"


def test_source_document_shape():
    e = UiStreamEmitter()
    e.source_document("123", "[1] 差旅制度")
    frame = parse_frame(e.drain()[0])
    assert frame == {
        "type": "source-document",
        "sourceId": "123",
        "mediaType": "text/markdown",
        "title": "[1] 差旅制度",
    }


def test_tool_frames():
    e = UiStreamEmitter()
    e.tool_input_start("c1", "retrieve_knowledge")
    e.tool_input_available("c1", "retrieve_knowledge", {"query": "发票"})
    e.tool_output_available("c1", "ok")
    frames = [parse_frame(l) for l in e.drain()]
    assert frames[0] == {"type": "tool-input-start", "toolCallId": "c1", "toolName": "retrieve_knowledge"}
    assert frames[1]["input"] == {"query": "发票"}
    assert frames[2] == {"type": "tool-output-available", "toolCallId": "c1", "output": "ok"}
