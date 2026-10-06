"""共享 ES 异步客户端 — 两个索引服务（kh_document / kh_chunk）共用。"""

from __future__ import annotations

import logging

from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: AsyncElasticsearch | None = None


def es_enabled() -> bool:
    return get_settings().elasticsearch_enabled


def get_es() -> AsyncElasticsearch | None:
    """已连接返回客户端；禁用或连不上返回 None（各服务自行降级）。"""
    global _client
    return _client


async def connect_es() -> AsyncElasticsearch | None:
    global _client
    if not es_enabled():
        logger.warning("Elasticsearch 已禁用，索引将跳过写入")
        return None
    cfg = get_settings()
    client = AsyncElasticsearch(cfg.elasticsearch_node)
    try:
        health = await client.cluster.health()
        logger.info("ES 已连接：%s, status=%s", cfg.elasticsearch_node, health.get("status"))
        _client = client
        return client
    except Exception as e:  # noqa: BLE001
        logger.warning("Elasticsearch 不可用，索引将跳过写入：%s", e)
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass
        return None


async def close_es() -> None:
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception:  # noqa: BLE001
            pass
        _client = None
