"""文档管线消费者 — 对应 document-pipeline.consumer.ts。"""

from __future__ import annotations

import json
import logging

from app.modules.mq import constants as mq
from app.modules.mq.rabbitmq import register_handler

logger = logging.getLogger(__name__)


async def _handle_rag(body: dict, raw: bytes) -> None:
    logger.info(
        "[RAG] type=%s, taskId=%s, documentIds=%s",
        body.get("type"), body.get("taskId"), json.dumps(body.get("documentIds") or []),
    )
    from app.modules.pipeline import orchestrator

    await orchestrator.handle_rag_reindex(body.get("type"), body.get("documentIds"))


async def _handle_search(body: dict, raw: bytes) -> None:
    logger.info(
        "[Search] type=%s, taskId=%s, documentId=%s",
        body.get("type"), body.get("taskId"), body.get("documentId"),
    )
    from app.modules.pipeline import orchestrator

    await orchestrator.handle_search_index(body.get("type"), body.get("documentId", ""))


async def _handle_kg(body: dict, raw: bytes) -> None:
    logger.info(
        "[KG] type=%s, taskId=%s, documentIds=%s",
        body.get("type"), body.get("taskId"), json.dumps(body.get("documentIds") or []),
    )
    from app.modules.pipeline import orchestrator

    await orchestrator.handle_kg_build(body.get("type"), body.get("documentIds"))


def register_all() -> None:
    register_handler(mq.RAG_REINDEX_QUEUE, _handle_rag)
    register_handler(mq.SEARCH_INDEX_QUEUE, _handle_search)
    register_handler(mq.KG_GRAPH_QUEUE, _handle_kg)
