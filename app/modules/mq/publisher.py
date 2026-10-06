"""文档管线生产者 — 对应 src/mq/document-pipeline.publisher.ts。投递失败只记日志，不回滚。"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.modules.mq import constants as mq

logger = logging.getLogger(__name__)


async def after_publish(document) -> None:
    document_id = str(document.id)
    await asyncio.gather(
        _trigger_rag_reindex(document_id),
        _trigger_search_index(document_id),
        _trigger_kg_build(document_id),
    )


async def after_unpublish(document_id: str) -> None:
    await asyncio.gather(
        _trigger_rag_delete(document_id),
        _trigger_search_delete(document_id),
        _trigger_kg_delete(document_id),
    )


async def _publish(exchange: str, routing_key: str, message: dict) -> bool:
    from app.modules.mq.rabbitmq import publish

    return await publish(exchange, routing_key, message)


async def _trigger_rag_reindex(document_id: str) -> None:
    message = {"taskId": str(uuid.uuid4()), "type": "BY_DOC_IDS", "documentIds": [document_id]}
    ok = await _publish(mq.RAG_REINDEX_EXCHANGE, mq.RAG_REINDEX_BY_IDS_RK, message)
    logger.info(
        "RAG 重建索引%s：documentId=%s, taskId=%s",
        "已投递" if ok else "投递失败", document_id, message["taskId"],
    )


async def _trigger_rag_delete(document_id: str) -> None:
    message = {"taskId": str(uuid.uuid4()), "type": "DELETE_BY_DOC_IDS", "documentIds": [document_id]}
    await _publish(mq.RAG_REINDEX_EXCHANGE, mq.RAG_REINDEX_DELETE_RK, message)


async def _trigger_search_index(document_id: str) -> None:
    """只投 documentId，消费者自行从 PG+Mongo 拉全文，避免 MQ 塞正文。"""
    message = {"taskId": str(uuid.uuid4()), "type": "INDEX", "documentId": document_id}
    ok = await _publish(mq.SEARCH_INDEX_EXCHANGE, mq.SEARCH_INDEX_DOCUMENT_RK, message)
    logger.info(
        "ES 搜索索引%s：documentId=%s, taskId=%s",
        "已投递" if ok else "投递失败", document_id, message["taskId"],
    )


async def _trigger_search_delete(document_id: str) -> None:
    message = {"taskId": str(uuid.uuid4()), "type": "DELETE", "documentId": document_id}
    await _publish(mq.SEARCH_INDEX_EXCHANGE, mq.SEARCH_INDEX_DELETE_RK, message)


async def _trigger_kg_build(document_id: str) -> None:
    message = {"taskId": str(uuid.uuid4()), "type": "BUILD_BY_DOC_IDS", "documentIds": [document_id]}
    ok = await _publish(mq.KG_GRAPH_EXCHANGE, mq.KG_GRAPH_BUILD_BY_IDS_RK, message)
    logger.info(
        "KG 建图%s：documentId=%s, taskId=%s",
        "已投递" if ok else "投递失败", document_id, message["taskId"],
    )


async def _trigger_kg_delete(document_id: str) -> None:
    message = {"taskId": str(uuid.uuid4()), "type": "DELETE_BY_DOC_IDS", "documentIds": [document_id]}
    await _publish(mq.KG_GRAPH_EXCHANGE, mq.KG_GRAPH_DELETE_RK, message)
