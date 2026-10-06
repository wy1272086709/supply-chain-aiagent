"""RAG 分块向量索引（ES kh_chunk, dense_vector cosine）— 对应 vector-index.service.ts。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.modules.documents.access import (
    ES_CHUNK_VISIBILITY_FIELDS,
    es_visibility_filter,
    wrap_es_query,
)
from app.modules.pipeline.es_client import get_es

logger = logging.getLogger(__name__)

CHUNK_INDEX = "kh_chunk"

_IK_TEXT = {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"}


async def ensure_index() -> None:
    es = get_es()
    if not es:
        return
    try:
        if await es.indices.exists(index=CHUNK_INDEX):
            await _ensure_visibility_mapping()
            return
        dims = get_settings().embedding_dimension
        await es.indices.create(
            index=CHUNK_INDEX,
            settings={"number_of_shards": 1, "number_of_replicas": 0, "refresh_interval": "5s"},
            mappings={
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "document_title": {**_IK_TEXT, "fields": {"keyword": {"type": "keyword"}}},
                    "content": _IK_TEXT,
                    "heading": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                    "total_chunks": {"type": "integer"},
                    "category_id": {"type": "keyword"},
                    "author_id": {"type": "keyword"},
                    "team_id": {"type": "keyword"},
                    "is_public": {"type": "boolean"},
                    "doc_status": {"type": "integer"},
                    "publish_time": {"type": "date"},
                    "indexed_at": {"type": "date"},
                    "embedding": {
                        "type": "dense_vector",
                        "dims": dims,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            },
        )
        logger.info("ES 索引创建成功：index=%s, dims=%d", CHUNK_INDEX, dims)
        await _ensure_visibility_mapping()
    except Exception as e:  # noqa: BLE001
        if "resource_already_exists" in str(e):
            return
        logger.warning("kh_chunk 索引初始化失败：%s", e)


async def _ensure_visibility_mapping() -> None:
    es = get_es()
    if not es:
        return
    try:
        await es.indices.put_mapping(
            index=CHUNK_INDEX,
            properties={
                "is_public": {"type": "boolean"},
                "author_id": {"type": "keyword"},
                "team_id": {"type": "keyword"},
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("kh_chunk 可见性 mapping 更新失败：%s", e)


async def update_visibility(document_id: str, vis: dict) -> None:
    es = get_es()
    if not es:
        logger.warning("跳过向量可见性更新（ES 不可用）：documentId=%s", document_id)
        return
    try:
        result = await es.update_by_query(
            index=CHUNK_INDEX,
            refresh=True,
            query={"term": {"document_id": document_id}},
            script={
                "source": (
                    "ctx._source.is_public = params.is_public; "
                    "ctx._source.team_id = params.team_id; "
                    "ctx._source.author_id = params.author_id;"
                ),
                "params": {
                    "is_public": vis["isPublic"],
                    "team_id": vis["teamId"],
                    "author_id": vis["authorId"],
                },
            },
        )
        logger.info(
            "向量块可见性已更新：documentId=%s, updated=%s",
            document_id, result.get("updated", 0),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("向量块可见性更新失败：documentId=%s, %s", document_id, e)


async def delete_by_doc_id(document_id: str) -> None:
    es = get_es()
    if not es:
        logger.warning("跳过删除向量块（ES 不可用）：documentId=%s", document_id)
        return
    try:
        await es.delete_by_query(
            index=CHUNK_INDEX, query={"term": {"document_id": document_id}}, refresh=True
        )
        logger.info("已从 ES 删除文档向量块：documentId=%s", document_id)
    except Exception as e:  # noqa: BLE001
        if "index_not_found" in str(e):
            return
        logger.error("ES 删除文档块失败：documentId=%s, error=%s", document_id, e)


async def index_chunks(chunks: list[dict]) -> None:
    if not chunks:
        return
    es = get_es()
    if not es:
        logger.warning("跳过向量索引写入（ES 不可用）：chunks=%d", len(chunks))
        return
    await ensure_index()

    operations: list[dict] = []
    for chunk in chunks:
        operations.append({"index": {"_index": CHUNK_INDEX, "_id": chunk["chunkId"]}})
        operations.append(_build_doc_map(chunk))

    response = await es.bulk(refresh=True, operations=operations)
    if response.get("errors"):
        failed = [
            f"{item.get('index', {}).get('_id')}: {item.get('index', {}).get('error', {}).get('reason', 'unknown')}"
            for item in response.get("items", [])
            if item.get("index", {}).get("error")
        ]
        logger.error("ES 批量索引部分失败：%s", ", ".join(failed))
        raise RuntimeError(f"ES 批量索引部分失败：{len(failed)} 条")
    logger.info("ES 批量索引成功：%d chunks → %s", len(chunks), CHUNK_INDEX)


def _clamp_top_k(top_k: int) -> int:
    return max(1, min(top_k, 50))


def _map_hits(hits: list[dict]) -> list[dict]:
    result = []
    for hit in hits:
        src = hit.get("_source") or {}
        result.append(
            {
                "chunkId": str(src.get("chunk_id") or hit.get("_id") or ""),
                "documentId": str(src.get("document_id") or ""),
                "documentTitle": str(src.get("document_title") or ""),
                "content": str(src.get("content") or ""),
                "heading": src.get("heading"),
                "score": hit.get("_score") or 0,
            }
        )
    return result


_SOURCE_FIELDS = ["chunk_id", "document_id", "document_title", "content", "heading"]


async def keyword_search(query: str, top_k: int = 20, scope: dict | None = None) -> list[dict]:
    es = get_es()
    if not es:
        logger.warning("跳过关键词检索（ES 不可用）")
        return []
    trimmed = (query or "").strip()
    if not trimmed:
        return []
    k = _clamp_top_k(top_k)
    vis = es_visibility_filter(scope, ES_CHUNK_VISIBILITY_FIELDS) if scope else None
    try:
        response = await es.search(
            index=CHUNK_INDEX,
            size=k,
            query=wrap_es_query(
                {
                    "multi_match": {
                        "query": trimmed,
                        "fields": ["document_title^2", "content"],
                        "analyzer": "ik_smart",
                    }
                },
                vis,
            ),
            source={"includes": _SOURCE_FIELDS},
        )
        return _map_hits(response.get("hits", {}).get("hits", []))
    except Exception as e:  # noqa: BLE001
        logger.warning("关键词检索失败：%s", e)
        return []


async def knn_search(query_vector: list[float], top_k: int = 20, scope: dict | None = None) -> list[dict]:
    es = get_es()
    if not es:
        logger.warning("跳过向量检索（ES 不可用）")
        return []
    if not query_vector:
        return []
    k = _clamp_top_k(top_k)
    vis = es_visibility_filter(scope, ES_CHUNK_VISIBILITY_FIELDS) if scope else None
    try:
        knn: dict = {
            "field": "embedding",
            "query_vector": query_vector,
            "k": k,
            "num_candidates": max(k * 10, 50),
        }
        if vis:
            knn["filter"] = vis
        response = await es.search(
            index=CHUNK_INDEX,
            size=k,
            knn=knn,
            source={"includes": _SOURCE_FIELDS},
        )
        return _map_hits(response.get("hits", {}).get("hits", []))
    except Exception as e:  # noqa: BLE001
        logger.warning("向量检索失败：%s", e)
        return []


async def search_hybrid(
    *,
    query: str,
    query_vector: list[float] | None = None,
    hybrid_top_k: int = 20,
    rrf_c: int = 60,
    scope: dict | None = None,
) -> list[dict]:
    """关键词 + 向量并行召回 → RRF 融合。嵌入失败时只走关键词。"""
    top_k = _clamp_top_k(hybrid_top_k or 20)
    c = rrf_c if rrf_c and rrf_c > 0 else 60

    keyword_hits, vector_hits = await _gather(
        keyword_search(query, top_k, scope),
        knn_search(query_vector, top_k, scope) if query_vector else _empty(),
    )
    fused = rrf_fuse(keyword_hits, vector_hits, c)
    logger.info(
        "混合检索 RRF：keyword=%d, vector=%d, fused=%d",
        len(keyword_hits), len(vector_hits), len(fused),
    )
    return fused


async def _gather(a_coro, b_coro):
    import asyncio

    return await asyncio.gather(a_coro, b_coro)


async def _empty():
    return []


def rrf_fuse(keyword_hits: list[dict], vector_hits: list[dict], rrf_c: int) -> list[dict]:
    """score(d) = Σ 1 / (C + rank + 1)；两路各按原始得分排序后算排名。"""
    fused: dict[str, dict] = {}

    def add_channel(hits: list[dict], channel: str) -> None:
        for rank, hit in enumerate(sorted(hits, key=lambda h: h["score"], reverse=True)):
            rrf = 1 / (rrf_c + rank + 1)
            existing = fused.get(hit["chunkId"])
            if not existing:
                fused[hit["chunkId"]] = {
                    **hit,
                    "score": rrf,
                    "bm25Score": hit["score"] if channel == "keyword" else 0,
                    "vectorScore": hit["score"] if channel == "vector" else 0,
                }
                continue
            existing["score"] += rrf
            if channel == "keyword":
                existing["bm25Score"] = hit["score"]
            if channel == "vector":
                existing["vectorScore"] = hit["score"]

    add_channel(keyword_hits, "keyword")
    add_channel(vector_hits, "vector")
    return sorted(fused.values(), key=lambda h: h["score"], reverse=True)


def _build_doc_map(chunk: dict) -> dict:
    doc: dict = {
        "chunk_id": chunk["chunkId"],
        "document_id": chunk["documentId"],
        "document_title": chunk["documentTitle"],
        "content": chunk["content"],
        "heading": chunk.get("heading"),
        "chunk_index": chunk.get("chunkIndex", 0),
        "total_chunks": chunk.get("totalChunks", 0),
        "category_id": chunk.get("categoryId"),
        "author_id": chunk.get("authorId"),
        "team_id": chunk.get("teamId"),
        "is_public": chunk.get("isPublic") or False,
        "doc_status": chunk.get("docStatus"),
        "publish_time": chunk.get("publishTime"),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }
    if chunk.get("embedding"):
        doc["embedding"] = chunk["embedding"]
    return doc
