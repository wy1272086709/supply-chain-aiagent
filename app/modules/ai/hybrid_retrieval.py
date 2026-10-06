"""kh_chunk 混合检索：向量 + 关键词 → RRF 粗融合 → reranker 精排 — 对应 hybrid-retrieval.service.ts。"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.modules.ai import reranker
from app.modules.documents.access import access_from_user
from app.modules.pipeline import embedding, vector_index

logger = logging.getLogger(__name__)


async def retrieve(query: str, top_k: int = 5, user: dict | None = None) -> list[dict]:
    cfg = get_settings()
    query_vector = await _embed_query(query)
    scope = access_from_user(user) if user else {"unrestricted": False, "userId": "", "teamIds": []}
    fused = await vector_index.search_hybrid(
        query=query,
        query_vector=query_vector,
        hybrid_top_k=cfg.rag_hybrid_top_k,
        rrf_c=cfg.rag_rrf_c,
        scope=scope,
    )

    if not fused:
        logger.info("混合检索无结果：queryLength=%d", len(query))
        return []

    reranked = await reranker.rerank(query, fused, len(fused))
    if reranked:
        kept = [h for h in reranked if h["score"] >= cfg.rag_min_score] if cfg.rag_min_score > 0 else reranked
        top = kept[:top_k]
        best = f"{reranked[0]['score']:.3f}" if reranked else "-"
        logger.info(
            "检索过滤：rerank=%d, minScore=%s, best=%s, kept=%d, return=%d",
            len(reranked), cfg.rag_min_score, best, len(kept), len(top),
        )
        return top

    return fused[:top_k]


async def _embed_query(query: str) -> list[float] | None:
    try:
        return await embedding.embed(query)
    except Exception as e:  # noqa: BLE001
        logger.warning("查询向量化失败，仅走关键词：%s", e)
        return None
