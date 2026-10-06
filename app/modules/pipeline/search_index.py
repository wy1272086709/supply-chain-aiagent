"""文档级全文搜索索引（ES kh_document）— 对应 search-index.service.ts。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.modules.documents.access import ES_DOC_VISIBILITY_FIELDS, es_visibility_filter
from app.modules.pipeline.es_client import get_es

logger = logging.getLogger(__name__)

ES_INDEX = "kh_document"

_IK_TEXT = {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"}

_MAPPINGS = {
    "properties": {
        "id": {"type": "keyword"},
        "title": _IK_TEXT,
        "summary": _IK_TEXT,
        "content": _IK_TEXT,
        "tags": {"type": "keyword"},
        "status": {"type": "integer"},
        "categoryId": {"type": "keyword"},
        "authorId": {"type": "keyword"},
        "teamId": {"type": "keyword"},
        "isPublic": {"type": "boolean"},
        "publishTime": {"type": "date"},
    }
}


async def ensure_index() -> None:
    es = get_es()
    if not es:
        return
    try:
        exists = await es.indices.exists(index=ES_INDEX)
        if exists:
            await _ensure_visibility_mapping()
            return
        await es.indices.create(index=ES_INDEX, mappings=_MAPPINGS)
        logger.info("已创建 ES 索引：%s", ES_INDEX)
        await _ensure_visibility_mapping()
    except Exception as e:  # noqa: BLE001
        logger.warning("kh_document 索引初始化失败：%s", e)


async def _ensure_visibility_mapping() -> None:
    es = get_es()
    if not es:
        return
    try:
        await es.indices.put_mapping(
            index=ES_INDEX,
            properties={
                "isPublic": {"type": "boolean"},
                "teamId": {"type": "keyword"},
                "authorId": {"type": "keyword"},
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("kh_document 可见性 mapping 更新失败：%s", e)


async def index_document(doc: dict) -> None:
    es = get_es()
    if not es:
        logger.warning("跳过搜索索引写入（ES 不可用）：documentId=%s", doc.get("id"))
        return
    document = {**doc, "indexedAt": datetime.now(timezone.utc).isoformat()}
    await es.index(index=ES_INDEX, id=str(doc["id"]), document=document, refresh=True)
    logger.info("搜索索引已写入 ES：documentId=%s", doc["id"])


async def update_visibility(document_id: str, vis: dict) -> None:
    es = get_es()
    if not es:
        logger.warning("跳过搜索可见性更新（ES 不可用）：documentId=%s", document_id)
        return
    try:
        await es.update(
            index=ES_INDEX,
            id=document_id,
            doc={
                "isPublic": vis["isPublic"],
                "teamId": vis["teamId"],
                "authorId": vis["authorId"],
            },
            refresh=True,
        )
        logger.info("搜索索引可见性已更新：documentId=%s", document_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("搜索索引可见性更新失败：documentId=%s, %s", document_id, e)


async def delete_document(document_id: str) -> None:
    es = get_es()
    if not es:
        logger.warning("跳过搜索索引删除（ES 不可用）：documentId=%s", document_id)
        return
    try:
        await es.delete(index=ES_INDEX, id=document_id, refresh=True)
    except Exception as e:  # noqa: BLE001
        if "404" not in str(e) and "404" not in str(getattr(e, "status_code", "")):
            logger.warning("ES 删除失败：documentId=%s, %s", document_id, e)
    logger.info("搜索索引已删除：documentId=%s", document_id)


async def search_documents(
    *,
    keyword: str,
    page: int = 1,
    page_size: int = 10,
    category_id: str | None = None,
    author_id: str | None = None,
    scope: dict | None = None,
) -> dict:
    page = page or 1
    page_size = min(page_size or 10, 50)
    offset = (page - 1) * page_size

    es = get_es()
    if not es:
        logger.warning("跳过搜索查询（ES 不可用）")
        return {"items": [], "total": 0, "page": page, "pageSize": page_size}

    filters: list[dict] = []
    vis = es_visibility_filter(scope, ES_DOC_VISIBILITY_FIELDS) if scope else None
    if vis:
        filters.append(vis)
    if category_id:
        filters.append({"term": {"categoryId": category_id}})
    if author_id:
        filters.append({"term": {"authorId": author_id}})

    multi_match = {
        "multi_match": {
            "query": keyword.strip(),
            "fields": ["title^3", "summary^2", "content"],
            "analyzer": "ik_smart",
        }
    }
    query = (
        {"bool": {"must": [multi_match], "filter": filters}} if filters else multi_match
    )

    try:
        response = await es.search(
            index=ES_INDEX,
            from_=offset,
            size=page_size,
            query=query,
            source={"excludes": ["content"]},
            highlight={
                "fields": {
                    "title": {"number_of_fragments": 0},
                    "content": {"fragment_size": 160, "number_of_fragments": 3},
                    "summary": {"fragment_size": 120, "number_of_fragments": 1},
                }
            },
        )
        total_raw = response.get("hits", {}).get("total", 0)
        total = total_raw if isinstance(total_raw, int) else (total_raw or {}).get("value", 0)

        items = []
        for hit in response.get("hits", {}).get("hits", []):
            src = hit.get("_source") or {}
            highlight = hit.get("highlight") or {}
            items.append(
                {
                    "id": str(src.get("id") or hit.get("_id")),
                    "title": src.get("title", ""),
                    "summary": src.get("summary"),
                    "categoryId": src.get("categoryId"),
                    "tags": src.get("tags"),
                    "authorId": src.get("authorId"),
                    "teamId": src.get("teamId"),
                    "isPublic": src.get("isPublic"),
                    "status": src.get("status"),
                    "publishTime": src.get("publishTime"),
                    "score": hit.get("_score") or 0,
                    "highlight": {
                        "title": highlight.get("title", []),
                        "summary": highlight.get("summary", []),
                        "content": highlight.get("content", []),
                    },
                }
            )
        return {"items": items, "total": total, "page": page, "pageSize": page_size}
    except Exception as e:  # noqa: BLE001
        logger.warning("搜索查询失败：%s", e)
        return {"items": [], "total": 0, "page": page, "pageSize": page_size}
