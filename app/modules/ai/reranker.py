"""DashScope text-rerank 重排 — 对应 reranker.service.ts。失败返回 None 由上层降级 RRF。"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    cfg = get_settings()
    return cfg.rag_rerank_enabled and bool(cfg.rerank_api_key_resolved)


def _to_document(hit: dict) -> str:
    heading = f"{hit.get('heading')}\n" if hit.get("heading") else ""
    text = f"{hit.get('documentTitle')}\n{heading}{hit.get('content')}".strip()
    return text[:2000] + "..." if len(text) > 2000 else text


async def rerank(query: str, candidates: list[dict], top_n: int) -> list[dict] | None:
    if not candidates:
        return []
    if not is_enabled():
        logger.warning("Reranker 未启用或未配置 Key，跳过重排")
        return None

    cfg = get_settings()
    endpoint = f"{cfg.rerank_base_url_resolved}/api/v1/services/rerank/text-rerank/text-rerank"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {cfg.rerank_api_key_resolved}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.rag_rerank_model,
                    "input": {"query": query, "documents": [_to_document(h) for h in candidates]},
                    "parameters": {
                        "return_documents": False,
                        "top_n": min(top_n, len(candidates)),
                    },
                },
            )
        body = response.json()
        if response.status_code != 200:
            logger.warning(
                "Rerank 调用失败：status=%d, code=%s, message=%s",
                response.status_code, body.get("code", ""), body.get("message", ""),
            )
            return None

        output = body.get("output") or {}
        results = output.get("results") or body.get("results") or []
        if not results:
            logger.warning("Rerank 返回空结果，降级为 RRF")
            return None

        reranked = [
            {**candidates[item["index"]], "score": item["relevance_score"]}
            for item in results
            if 0 <= item["index"] < len(candidates)
        ]
        logger.info(
            "Rerank 完成：model=%s, in=%d, out=%d", cfg.rag_rerank_model, len(candidates), len(reranked)
        )
        return reranked
    except Exception as e:  # noqa: BLE001
        logger.warning("Rerank 异常，降级为 RRF：%s", e)
        return None
