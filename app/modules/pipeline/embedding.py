"""文本向量化 — 对应 embedding.service.ts（langchain-openai OpenAIEmbeddings → DashScope）。

未配置 Key 时构造抛错（与 TS 一致）；批大小钳制 ≤10（DashScope text-embedding-v3 上限）。
"""

from __future__ import annotations

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_embeddings = None


def _get_embeddings():
    global _embeddings
    if _embeddings is not None:
        return _embeddings
    from langchain_openai import OpenAIEmbeddings

    cfg = get_settings()
    api_key = cfg.embedding_api_key_resolved
    if not api_key:
        raise RuntimeError("未配置 EMBEDDING_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY")

    configured_batch = cfg.embedding_batch_size
    if configured_batch > 10:
        logger.warning("EMBEDDING_BATCH_SIZE=%d 超过 DashScope 上限，已钳制为 10", configured_batch)

    _embeddings = OpenAIEmbeddings(
        api_key=api_key,
        model=cfg.embedding_model,
        dimensions=cfg.embedding_dimension,
        base_url=cfg.embedding_base_url,
        check_embedding_ctx_length=False,  # 避免 tiktoken 分片改写 DashScope 请求
    )
    return _embeddings


def _dashscope_batch_limit() -> int:
    """DashScope text-embedding-v3 单批 ≤10 条；langchain-openai 1.x 已移除 batch_size 参数，分片在这里做。"""
    configured = get_settings().embedding_batch_size
    return min(configured if configured > 0 else 10, 10)


async def embed(text: str) -> list[float]:
    vectors = await embed_batch([text])
    return vectors[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    vectors: list[list[float]] = []
    embeddings = _get_embeddings()
    limit = _dashscope_batch_limit()
    for i in range(0, len(texts), limit):
        vectors.extend(await embeddings.aembed_documents(texts[i : i + limit]))
    return vectors
