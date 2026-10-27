"""Token 计量代理 — 透明转发 DashScope 并记录每次调用的 usage。

用法（配合 measure_tokens.py，勿单独使用生产流量）：
    uv run python evaluation/token_proxy.py          # 起在 127.0.0.1:9090
然后以环境变量覆盖重启应用，把三路调用都指向代理：
    OPENAI_BASE_URL=http://127.0.0.1:9090/compatible-mode/v1
    EMBEDDING_BASE_URL=http://127.0.0.1:9090/compatible-mode/v1
    RERANK_BASE_URL=http://127.0.0.1:9090

日志：results/token_proxy_log.jsonl，每行：
    {ts, kind, model, prompt_tokens, completion_tokens, total_tokens}
kind 按请求特征分类：router / grader / rewriter / generation / embedding / rerank / other
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response

UPSTREAM = "https://dashscope.aliyuncs.com"
LOG_PATH = Path(__file__).parent / "results" / "token_proxy_log.jsonl"
LOG_PATH.parent.mkdir(exist_ok=True)
LOG_PATH.write_text("", encoding="utf-8")  # 每次启动重置

app = FastAPI()
_client = httpx.AsyncClient(timeout=180)

SYSTEM_PREFIXES = (
    ("你是企业知识库的意图与检索改写器", "router"),
    ("你是企业知识库的检索评估器", "grader"),
    ("你是企业知识库的检索改写器", "rewriter"),
    ("你是企业知识库助手", "generation"),
)


def _classify(path: str, body: dict) -> tuple[str, str]:
    """(kind, model)。chat 调用按系统提示词前缀区分链路角色。"""
    model = str(body.get("model") or "")
    if path.endswith("/chat/completions"):
        messages = body.get("messages") or []
        first = str((messages[0] or {}).get("content") or "") if messages else ""
        for prefix, kind in SYSTEM_PREFIXES:
            if first.startswith(prefix):
                return kind, model
        return "chat_other", model
    if path.endswith("/embeddings"):
        return "embedding", model
    if "rerank" in path:
        return "rerank", model
    return "other", model


def _usage(resp_body: dict) -> tuple[int, int, int]:
    usage = resp_body.get("usage") or (resp_body.get("output") or {}).get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    return prompt, completion, total


def _log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@app.api_route("/{path:path}", methods=["POST", "GET"])
async def proxy(path: str, request: Request) -> Response:
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    resp = await _client.request(request.method, f"{UPSTREAM}/{path}", content=body, headers=headers)

    try:
        # 分类看【请求体】（messages 在请求里）；usage 看【响应体】
        req_parsed = json.loads(body) if body else {}
        kind, model = _classify(path, req_parsed)
        resp_parsed = json.loads(resp.content) if resp.content else {}
        prompt, completion, total = _usage(resp_parsed)
        _log({
            "ts": time.time(),
            "kind": kind,
            "model": model or "-",
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        })
    except Exception:  # noqa: BLE001 计量失败不影响转发
        pass

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9090, log_level="warning")
