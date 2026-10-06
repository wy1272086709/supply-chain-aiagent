"""KG 知识图谱构建（Neo4j）— 对应 graph-build.service.ts，Cypher 逐字移植。

图模型：(KnowledgeDocument)-[:HAS_CHUNK]->(DocumentChunk)-[:MENTIONS]->(KnowledgeEntity)，
(KnowledgeEntity)-[:RELATED_TO]->(KnowledgeEntity)。Neo4j 不可用时跳过写入。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.core.config import get_settings
from app.modules.documents.access import neo4j_access_params, neo4j_document_access_where
from app.modules.pipeline import chunking

logger = logging.getLogger(__name__)

_driver = None


def _enabled() -> bool:
    return get_settings().neo4j_enabled


async def connect() -> None:
    global _driver
    if not _enabled():
        logger.warning("Neo4j 已禁用（NEO4J_ENABLED=false）")
        return
    from neo4j import AsyncGraphDatabase

    cfg = get_settings()
    driver = AsyncGraphDatabase.driver(cfg.neo4j_uri, auth=(cfg.neo4j_user, cfg.neo4j_password))
    try:
        await driver.verify_connectivity()
        logger.info("Neo4j 已连接：%s", cfg.neo4j_uri)
        _driver = driver
    except Exception as e:  # noqa: BLE001
        logger.warning("Neo4j 不可用，KG 写入将跳过：%s", e)
        try:
            await driver.close()
        except Exception:  # noqa: BLE001
            pass


async def close() -> None:
    global _driver
    if _driver is not None:
        try:
            await _driver.close()
        except Exception:  # noqa: BLE001
            pass
        _driver = None


def _to_number(value, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


# ---------------- 对话图谱上下文助手 ----------------


def format_graph_context(hit: dict) -> str:
    """给模型看的图谱上下文：只写关系与来源，不当制度原文。"""
    if not hit["entities"] and not hit["relations"]:
        return ""
    lines: list[str] = []
    if hit["relations"]:
        lines.append("关系：")
        for rel in hit["relations"]:
            lines.append(f"- {rel['source']} → {rel['target']}（{rel['relation']}）")
    else:
        lines.append("实体：" + "、".join(e["name"] for e in hit["entities"]))
    if hit["documents"]:
        titles = [d["title"] for d in hit["documents"] if d.get("title")]
        if titles:
            lines.append("来源文档：" + "；".join(titles))
    return "\n".join(lines)


def format_graph_system_text(hit: dict) -> str:
    body = format_graph_context(hit)
    if not body:
        return ""
    return (
        f"【本轮知识图谱】\n{body}\n\n"
        "以上只说明实体之间的关系，不能当制度原文；制度/流程以知识库检索资料为准。"
    )


def merge_graph_hits(base: dict, extra: dict) -> dict:
    names = {e["name"] for e in base["entities"]}
    entities = list(base["entities"])
    for entity in extra["entities"]:
        if entity["name"] in names:
            continue
        names.add(entity["name"])
        entities.append(entity)

    def rel_key(r: dict) -> str:
        return f"{r['source']}\t{r['relation']}\t{r['target']}"

    rel_keys = {rel_key(r) for r in base["relations"]}
    relations = list(base["relations"])
    for rel in extra["relations"]:
        key = rel_key(rel)
        if key in rel_keys:
            continue
        rel_keys.add(key)
        relations.append(rel)

    doc_ids = {d["documentId"] for d in base["documents"]}
    documents = list(base["documents"])
    for doc in extra["documents"]:
        if doc["documentId"] in doc_ids:
            continue
        doc_ids.add(doc["documentId"])
        documents.append(doc)

    queries = [
        q.strip()
        for q in dict.fromkeys(
            [p for p in base["query"].split("/")] + [p for p in extra["query"].split("/")]
        )
        if q.strip()
    ]
    return {"query": " / ".join(queries), "entities": entities, "relations": relations, "documents": documents}


def split_graph_keywords(keyword: str | list[str]) -> list[str]:
    """模型常把「发票 报销」写成一项；图匹配要拆成短名，否则对不上节点。"""
    seen: set[str] = set()
    kws: list[str] = []
    parts = keyword if isinstance(keyword, list) else [keyword]
    for part in parts:
        for raw in re.split(r"[\s/]+", str(part)):
            kw = raw.strip()
            if not kw:
                continue
            key = kw.lower()
            if key in seen:
                continue
            seen.add(key)
            kws.append(kw)
    return kws


# ---------------- 构建写入 ----------------


async def build_for_document(doc: dict) -> int:
    if _driver is None:
        logger.warning("跳过 KG 构建（Neo4j 不可用）：documentId=%s", doc.get("id"))
        return 0
    if not (doc.get("content") or "").strip():
        logger.info("文档内容为空，跳过 KG：documentId=%s", doc["id"])
        return 0

    # 先清再建，避免重复发布导致边/节点翻倍
    await delete_for_document(doc["id"])

    now = datetime.now(timezone.utc).isoformat()
    async with _driver.session() as session:
        # ① 文档节点：按 id 幂等 upsert，保留首次 createdAt
        await session.run(
            """
            MERGE (d:KnowledgeDocument {id: $id})
            SET d.title = $title, d.summary = $summary, d.categoryId = $categoryId,
                d.authorId = $authorId, d.teamId = $teamId, d.isPublic = $isPublic,
                d.status = $status, d.tags = $tags,
                d.updatedAt = $now, d.createdAt = coalesce(d.createdAt, $now)
            """,
            {
                "id": doc["id"],
                "title": doc["title"],
                "summary": doc.get("summary") or "",
                "categoryId": doc.get("categoryId"),
                "authorId": doc.get("authorId"),
                "teamId": doc.get("teamId"),
                "isPublic": doc.get("isPublic") or False,
                "status": doc["status"],
                "tags": doc.get("tags") or "",
                "now": now,
            },
        )

        # ② 复用 RAG 同款分块，保证图谱粒度与向量块一致
        publish_time = doc.get("publishTime")
        if isinstance(publish_time, datetime):
            publish_time = publish_time.isoformat()
        chunks = chunking.chunk(
            content=doc["content"],
            document_id=doc["id"],
            document_title=doc["title"],
            category_id=doc.get("categoryId"),
            author_id=doc.get("authorId"),
            team_id=doc.get("teamId"),
            is_public=doc.get("isPublic"),
            doc_status=doc.get("status"),
            publish_time=publish_time,
        )

        from app.modules.pipeline import extraction

        total_entities = 0
        for chunk in chunks:
            # ③ chunk 节点 + 文档→块边
            await session.run(
                """
                MERGE (c:DocumentChunk {chunkId: $chunkId})
                SET c.documentId = $documentId, c.content = $content, c.heading = $heading,
                    c.chunkIndex = $chunkIndex, c.totalChunks = $totalChunks, c.updatedAt = $now
                WITH c
                MATCH (d:KnowledgeDocument {id: $documentId})
                MERGE (d)-[r:HAS_CHUNK]->(c)
                SET r.chunkIndex = $chunkIndex
                """,
                {
                    "chunkId": chunk["chunkId"],
                    "documentId": doc["id"],
                    "content": chunk["content"],
                    "heading": chunk.get("heading"),
                    "chunkIndex": chunk["chunkIndex"],
                    "totalChunks": chunk["totalChunks"],
                    "now": now,
                },
            )

            # ④ 抽实体关系并落图；单块失败不阻断其余块
            try:
                extracted = await extraction.extract(chunk["content"], chunk.get("heading"), doc["title"])
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "KG 抽取失败，跳过该块：documentId=%s, chunk=%s, %s",
                    doc["id"], chunk["chunkIndex"], e,
                )
                extracted = {"entities": [], "relations": []}
            extracted["chunkId"] = chunk["chunkId"]
            total_entities += await _write_extraction(session, extracted)

        logger.info(
            "KG 图谱构建完成：documentId=%s, chunks=%d, entities=%d",
            doc["id"], len(chunks), total_entities,
        )
        return total_entities


async def build_batch(docs: list[dict]) -> None:
    for doc in docs:
        try:
            await build_for_document(doc)
        except Exception as e:  # noqa: BLE001
            logger.error("KG 构建失败：documentId=%s, %s", doc.get("id"), e)


async def update_visibility(document_id: str, vis: dict) -> None:
    if _driver is None:
        logger.warning("跳过图谱可见性更新（Neo4j 不可用）：documentId=%s", document_id)
        return
    try:
        async with _driver.session() as session:
            await session.run(
                """
                MATCH (d:KnowledgeDocument {id: $id})
                SET d.isPublic = $isPublic, d.teamId = $teamId, d.authorId = $authorId,
                    d.updatedAt = $now
                """,
                {
                    "id": document_id,
                    "isPublic": vis["isPublic"],
                    "teamId": vis["teamId"],
                    "authorId": vis["authorId"],
                    "now": datetime.now(timezone.utc).isoformat(),
                },
            )
        logger.info("图谱可见性已更新：documentId=%s", document_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("图谱可见性更新失败：documentId=%s, %s", document_id, e)


async def delete_for_document(document_id: str) -> None:
    if _driver is None:
        return
    async with _driver.session() as session:
        # 删除文档节点及其所有 chunk（DETACH 一并拆掉相连关系边）
        await session.run(
            """
            MATCH (d:KnowledgeDocument {id: $id})
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:DocumentChunk)
            DETACH DELETE c, d
            """,
            {"id": document_id},
        )
        # 孤儿实体清理：没有任何 chunk MENTIONS 的实体视为无引用
        await session.run(
            """
            MATCH (e:KnowledgeEntity)
            WHERE NOT (e)<-[:MENTIONS]-()
            DETACH DELETE e
            """
        )
    logger.info("KG 图谱已删除：documentId=%s", document_id)


async def _write_extraction(session, result: dict) -> int:
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for entity in result["entities"]:
        await session.run(
            """
            MERGE (e:KnowledgeEntity {name: $name})
            ON CREATE SET e.type = $type, e.description = $description,
                          e.aliases = $aliases, e.createdAt = $now, e.updatedAt = $now
            ON MATCH SET e.type = coalesce($type, e.type),
                         e.description = CASE WHEN $description <> '' THEN $description ELSE e.description END,
                         e.updatedAt = $now
            """,
            {
                "name": entity["name"],
                "type": entity.get("type"),
                "description": entity.get("description") or "",
                "aliases": entity.get("aliases") or [],
                "now": now,
            },
        )
        count += 1
        if result.get("chunkId"):
            await session.run(
                """
                MATCH (c:DocumentChunk {chunkId: $chunkId})
                MATCH (e:KnowledgeEntity {name: $name})
                MERGE (c)-[:MENTIONS]->(e)
                """,
                {"chunkId": result["chunkId"], "name": entity["name"]},
            )

    for rel in result["relations"]:
        await session.run(
            """
            MATCH (a:KnowledgeEntity {name: $source})
            MATCH (b:KnowledgeEntity {name: $target})
            MERGE (a)-[r:RELATED_TO]->(b)
            ON CREATE SET r.relation = $relType, r.weight = $weight, r.createdAt = datetime()
            ON MATCH SET r.weight = coalesce($weight, r.weight)
            """,
            {
                "source": rel["source"],
                "target": rel["target"],
                "relType": rel["relation"],
                "weight": rel.get("weight", 0.5),
            },
        )
    return count


# ---------------- 读 API ----------------


async def list_nodes(node_type: str | None = None, limit: int = 200, scope: dict | None = None) -> list[dict]:
    if _driver is None:
        logger.warning("跳过图谱节点查询（Neo4j 不可用）")
        return []
    cap = max(1, min(limit, 500))
    try:
        async with _driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:KnowledgeEntity)
                WHERE ($type IS NULL OR $type = '' OR e.type = $type)
                  AND (
                    $unrestricted OR EXISTS {{
                      MATCH (d:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e)
                      WHERE {neo4j_document_access_where('d')}
                    }}
                  )
                RETURN DISTINCT e.name AS id, e.name AS name, e.type AS type,
                       e.description AS description
                LIMIT $limit
                """,
                {"type": node_type or None, "limit": cap, **neo4j_access_params(scope)},
            )
            records = await result.data()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r.get("type"),
                "description": r.get("description"),
            }
            for r in records
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("图谱节点查询失败：%s", e)
        return []


async def list_edges(limit: int = 500, scope: dict | None = None) -> list[dict]:
    if _driver is None:
        logger.warning("跳过图谱边查询（Neo4j 不可用）")
        return []
    cap = max(1, min(limit, 1000))
    try:
        async with _driver.session() as session:
            result = await session.run(
                f"""
                MATCH (a:KnowledgeEntity)-[r:RELATED_TO]->(b:KnowledgeEntity)
                WHERE $unrestricted OR (
                  EXISTS {{
                    MATCH (d1:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(a)
                    WHERE {neo4j_document_access_where('d1')}
                  }}
                  AND EXISTS {{
                    MATCH (d2:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(b)
                    WHERE {neo4j_document_access_where('d2')}
                  }}
                )
                RETURN a.name AS source, b.name AS target,
                       r.relation AS relation, r.weight AS weight
                LIMIT $limit
                """,
                {"limit": cap, **neo4j_access_params(scope)},
            )
            records = await result.data()
        return [
            {
                "source": r["source"],
                "target": r["target"],
                "relation": r.get("relation") or "RELATED_TO",
                "weight": _to_number(r.get("weight"), 0.5),
            }
            for r in records
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("图谱边查询失败：%s", e)
        return []


async def search_graph(keyword: str, limit: int = 50, scope: dict | None = None) -> list[dict]:
    if _driver is None:
        logger.warning("跳过图谱检索（Neo4j 不可用）")
        return []
    kw = (keyword or "").strip()
    if not kw:
        return []
    cap = max(1, min(limit, 200))
    try:
        async with _driver.session() as session:
            result = await session.run(
                f"""
                MATCH (n)
                WHERE (
                     toLower(coalesce(n.name, '')) CONTAINS toLower($kw)
                  OR toLower(coalesce(n.title, '')) CONTAINS toLower($kw)
                  OR toLower(coalesce(n.heading, '')) CONTAINS toLower($kw)
                  OR toLower(coalesce(n.description, '')) CONTAINS toLower($kw)
                  OR toLower(coalesce(n.summary, '')) CONTAINS toLower($kw)
                  OR toLower(coalesce(n.content, '')) CONTAINS toLower($kw)
                )
                AND (
                  $unrestricted
                  OR (n:KnowledgeDocument AND {neo4j_document_access_where('n')})
                  OR (n:DocumentChunk AND EXISTS {{
                    MATCH (d:KnowledgeDocument)-[:HAS_CHUNK]->(n)
                    WHERE {neo4j_document_access_where('d')}
                  }})
                  OR (n:KnowledgeEntity AND EXISTS {{
                    MATCH (d:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(n)
                    WHERE {neo4j_document_access_where('d')}
                  }})
                )
                RETURN labels(n)[0] AS label,
                       coalesce(n.name, n.title, n.heading, n.id, n.chunkId) AS name,
                       coalesce(n.id, n.chunkId, n.name) AS id,
                       n.type AS type,
                       n.title AS title,
                       n.description AS description,
                       n.heading AS heading,
                       n.documentId AS documentId,
                       n.summary AS summary,
                       CASE
                         WHEN n.content IS NULL THEN null
                         ELSE substring(n.content, 0, 160)
                       END AS snippet
                ORDER BY label, name
                LIMIT $limit
                """,
                {"kw": kw, "limit": cap, **neo4j_access_params(scope)},
            )
            records = await result.data()
        return [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "label": r.get("label"),
                "type": r.get("type"),
                "title": r.get("title"),
                "description": r.get("description"),
                "heading": r.get("heading"),
                "documentId": r.get("documentId"),
                "summary": r.get("summary"),
                "snippet": r.get("snippet"),
            }
            for r in records
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("图谱检索失败：%s", e)
        return []


async def retrieve_for_chat(keyword, limit: int = 8, scope: dict | None = None) -> dict:
    """按短实体名找可见实体（相等/前缀/后缀），再查关系与来源文档。"""
    kws = split_graph_keywords(keyword)
    empty = {"query": " / ".join(kws), "entities": [], "relations": [], "documents": []}
    if _driver is None:
        logger.warning("跳过图谱对话检索（Neo4j 不可用）")
        return empty
    if not kws:
        return empty

    cap = max(1, min(limit, 20))
    vis = neo4j_access_params(scope)
    try:
        async with _driver.session() as session:
            ent_result = await session.run(
                f"""
                MATCH (e:KnowledgeEntity)
                WHERE any(kw IN $kws WHERE
                  toLower(e.name) = toLower(kw)
                  OR toLower(replace(replace(e.name, '《', ''), '》', '')) STARTS WITH toLower(kw)
                  OR toLower(e.name) ENDS WITH toLower(kw)
                  OR any(a IN coalesce(e.aliases, []) WHERE
                    toLower(toString(a)) = toLower(kw)
                    OR toLower(toString(a)) STARTS WITH toLower(kw)
                    OR toLower(toString(a)) ENDS WITH toLower(kw)
                  )
                )
                AND (
                  $unrestricted OR EXISTS {{
                    MATCH (d:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e)
                    WHERE {neo4j_document_access_where('d')}
                  }}
                )
                RETURN e.name AS name, e.type AS type, e.description AS description
                ORDER BY
                  CASE
                    WHEN any(kw IN $kws WHERE toLower(e.name) = toLower(kw)) THEN 0
                    WHEN any(kw IN $kws WHERE toLower(e.name) STARTS WITH toLower(kw)
                      OR toLower(e.name) ENDS WITH toLower(kw)) THEN 1
                    ELSE 2
                  END,
                  size(e.name)
                LIMIT $limit
                """,
                {"kws": kws, "limit": cap, **vis},
            )
            ent_records = await ent_result.data()
            entities = [
                {"name": r["name"], "type": r.get("type"), "description": r.get("description")}
                for r in ent_records
            ]
            names = [e["name"] for e in entities]
            if not names:
                logger.info("图谱对话检索无实体：kws=%s", "/".join(kws))
                return empty

            rel_result = await session.run(
                """
                MATCH (a:KnowledgeEntity)-[r:RELATED_TO]->(b:KnowledgeEntity)
                WHERE a.name IN $names AND b.name IN $names
                RETURN a.name AS source,
                       coalesce(nullif(r.relation, 'RELATED_TO'), '关联') AS relation,
                       b.name AS target
                """,
                {"names": names},
            )
            relations = []
            seen: set[str] = set()
            for r in await rel_result.data():
                key = f"{r['source']}\t{r['relation']}\t{r['target']}"
                if key in seen:
                    continue
                seen.add(key)
                relations.append({"source": r["source"], "relation": r["relation"], "target": r["target"]})

            doc_result = await session.run(
                f"""
                MATCH (d:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e:KnowledgeEntity)
                WHERE e.name IN $names
                  AND ($unrestricted OR {neo4j_document_access_where('d')})
                RETURN DISTINCT d.id AS documentId, d.title AS title
                LIMIT 8
                """,
                {"names": names, **vis},
            )
            documents = [
                {"documentId": r["documentId"], "title": r.get("title") or ""}
                for r in await doc_result.data()
            ]

        logger.info(
            "图谱对话检索：kws=%s entities=%d rels=%d",
            "/".join(kws), len(entities), len(relations),
        )
        return {"query": " / ".join(kws), "entities": entities, "relations": relations, "documents": documents}
    except Exception as e:  # noqa: BLE001
        logger.warning("图谱对话检索失败：%s", e)
        return empty


async def get_overview(
    *,
    keyword: str | None = None,
    entity_type: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    doc_limit: int = 24,
    scope: dict | None = None,
) -> dict:
    empty = {
        "nodes": [],
        "edges": [],
        "stats": {
            "nodeCount": 0, "edgeCount": 0, "documentCount": 0, "entityCount": 0,
            "tagCount": 0, "mentionCount": 0, "relatedCount": 0, "entityTypes": [],
        },
        "topEntities": [],
        "recentNodes": [],
        "entityTypes": [],
    }
    if _driver is None:
        logger.warning("跳过图谱全景（Neo4j 不可用）")
        return empty

    kw = (keyword or "").strip()
    entity_type = (entity_type or "").strip() or None
    from_ = (from_ or "").strip() or None
    to = (to or "").strip() or None
    doc_limit = max(1, min(doc_limit if doc_limit is not None else 24, 80))
    vis = neo4j_access_params(scope)

    try:
        async with _driver.session() as session:
            stats_result = await session.run(
                f"""
                OPTIONAL MATCH (d:KnowledgeDocument)
                WHERE {neo4j_document_access_where('d')}
                WITH count(d) AS documentCount
                OPTIONAL MATCH (d2:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e:KnowledgeEntity)
                WHERE {neo4j_document_access_where('d2')}
                WITH documentCount, count(DISTINCT e) AS entityCount
                OPTIONAL MATCH (a:KnowledgeEntity)-[rel:RELATED_TO]->(b:KnowledgeEntity)
                WHERE $unrestricted OR (
                  EXISTS {{
                    MATCH (d3:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(a)
                    WHERE {neo4j_document_access_where('d3')}
                  }}
                  AND EXISTS {{
                    MATCH (d4:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(b)
                    WHERE {neo4j_document_access_where('d4')}
                  }}
                )
                WITH documentCount, entityCount, count(rel) AS relatedCount
                OPTIONAL MATCH (d5:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e0:KnowledgeEntity)
                WHERE {neo4j_document_access_where('d5')}
                RETURN documentCount, entityCount, relatedCount, count(e0) AS mentionCount
                """,
                vis,
            )
            stats_rows = await stats_result.data()
            row = stats_rows[0] if stats_rows else {}
            document_count = _to_number(row.get("documentCount"), 0)
            entity_count = _to_number(row.get("entityCount"), 0)
            related_count = _to_number(row.get("relatedCount"), 0)
            mention_count = _to_number(row.get("mentionCount"), 0)

            type_result = await session.run(
                f"""
                MATCH (d:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e:KnowledgeEntity)
                WHERE {neo4j_document_access_where('d')}
                  AND e.type IS NOT NULL AND e.type <> ''
                RETURN e.type AS type, count(DISTINCT e) AS count
                ORDER BY count DESC
                """,
                vis,
            )
            entity_types = [
                {"type": str(r["type"]), "count": _to_number(r.get("count"), 0)}
                for r in await type_result.data()
            ]

            top_result = await session.run(
                f"""
                MATCH (e:KnowledgeEntity)<-[:MENTIONS]-(:DocumentChunk)<-[:HAS_CHUNK]-(d:KnowledgeDocument)
                WHERE {neo4j_document_access_where('d')}
                RETURN e.name AS name, e.type AS type, count(*) AS degree
                ORDER BY degree DESC
                LIMIT 5
                """,
                vis,
            )
            top_entities = [
                {"name": str(r["name"]), "type": r.get("type"), "degree": _to_number(r.get("degree"), 0)}
                for r in await top_result.data()
            ]

            recent_result = await session.run(
                f"""
                MATCH (d:KnowledgeDocument)
                WHERE {neo4j_document_access_where('d')}
                RETURN d.id AS id, d.title AS name, d.updatedAt AS updatedAt
                ORDER BY d.updatedAt DESC
                LIMIT 8
                """,
                vis,
            )
            recent_nodes = [
                {
                    "id": f"doc:{r['id']}",
                    "name": str(r.get("name") or ""),
                    "kind": "document",
                    "updatedAt": r.get("updatedAt"),
                }
                for r in await recent_result.data()
            ]

            doc_result = await session.run(
                f"""
                MATCH (d:KnowledgeDocument)
                WHERE {neo4j_document_access_where('d')}
                  AND ($kw = '' OR toLower(coalesce(d.title, '')) CONTAINS toLower($kw)
                      OR toLower(coalesce(d.summary, '')) CONTAINS toLower($kw)
                      OR toLower(coalesce(d.tags, '')) CONTAINS toLower($kw))
                  AND ($from IS NULL OR d.updatedAt >= $from)
                  AND ($to IS NULL OR d.updatedAt <= $to)
                WITH d ORDER BY d.updatedAt DESC LIMIT $docLimit
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e:KnowledgeEntity)
                WHERE $entityType IS NULL OR e.type = $entityType
                RETURN d.id AS docId, d.title AS docTitle, d.summary AS summary,
                       d.tags AS tags, d.updatedAt AS updatedAt,
                       collect(DISTINCT CASE WHEN e IS NULL THEN NULL ELSE {{
                         name: e.name, type: e.type, description: e.description
                       }} END) AS entities
                """,
                {"kw": kw, "entityType": entity_type, "from": from_, "to": to, "docLimit": doc_limit, **vis},
            )
            doc_records = await doc_result.data()

            # 关键词命中实体但标题未命中时，把提及该实体的文档补进来
            if kw:
                extra_result = await session.run(
                    f"""
                    MATCH (e:KnowledgeEntity)<-[:MENTIONS]-(:DocumentChunk)<-[:HAS_CHUNK]-(d:KnowledgeDocument)
                    WHERE {neo4j_document_access_where('d')}
                      AND (toLower(coalesce(e.name, '')) CONTAINS toLower($kw)
                       OR toLower(coalesce(e.description, '')) CONTAINS toLower($kw))
                    WITH DISTINCT d
                    WHERE ($from IS NULL OR d.updatedAt >= $from)
                      AND ($to IS NULL OR d.updatedAt <= $to)
                    OPTIONAL MATCH (d)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e2:KnowledgeEntity)
                    WHERE $entityType IS NULL OR e2.type = $entityType
                    RETURN d.id AS docId, d.title AS docTitle, d.summary AS summary,
                           d.tags AS tags, d.updatedAt AS updatedAt,
                           collect(DISTINCT CASE WHEN e2 IS NULL THEN NULL ELSE {{
                             name: e2.name, type: e2.type, description: e2.description
                           }} END) AS entities
                    LIMIT $docLimit
                    """,
                    {"kw": kw, "entityType": entity_type, "from": from_, "to": to, "docLimit": doc_limit, **vis},
                )
                seen = {str(r["docId"]) for r in doc_records}
                for r in await extra_result.data():
                    if str(r["docId"]) not in seen:
                        doc_records.append(r)

        node_map: dict[str, dict] = {}
        edge_map: dict[str, dict] = {}
        entity_names: set[str] = set()

        def add_edge(source: str, target: str, relation: str, kind: str) -> None:
            key = f"{kind}|{source}|{target}|{relation}"
            if key not in edge_map:
                edge_map[key] = {"source": source, "target": target, "relation": relation, "kind": kind}

        def split_tags(raw) -> list[str]:
            return [t.strip() for t in re.split(r"[,，]", str(raw or "")) if t.strip()]

        for record in doc_records:
            doc_id = str(record["docId"])
            doc_node_id = f"doc:{doc_id}"
            node_map[doc_node_id] = {
                "id": doc_node_id,
                "name": str(record.get("docTitle") or ""),
                "kind": "document",
                "type": "DOCUMENT",
                "documentId": doc_id,
                "updatedAt": record.get("updatedAt"),
                "description": record.get("summary"),
            }
            for tag in split_tags(record.get("tags")):
                tag_id = f"tag:{tag}"
                node_map[tag_id] = {"id": tag_id, "name": tag, "kind": "tag", "type": "TAG"}
                add_edge(doc_node_id, tag_id, "标注", "tagged")
            for entity in record.get("entities") or []:
                if not entity or not entity.get("name"):
                    continue
                entity_id = f"entity:{entity['name']}"
                entity_names.add(entity["name"])
                node_map[entity_id] = {
                    "id": entity_id,
                    "name": entity["name"],
                    "kind": "entity",
                    "type": entity.get("type") or "CONCEPT",
                    "description": entity.get("description"),
                }
                add_edge(doc_node_id, entity_id, "提及", "mentions")

        if entity_names:
            # 只取当前画布上实体之间的 RELATED_TO，避免拉全库关系
            async with _driver.session() as session:
                related_result = await session.run(
                    """
                    MATCH (a:KnowledgeEntity)-[r:RELATED_TO]->(b:KnowledgeEntity)
                    WHERE a.name IN $names AND b.name IN $names
                    RETURN a.name AS source, b.name AS target,
                           r.relation AS relation, r.weight AS weight
                    LIMIT 400
                    """,
                    {"names": list(entity_names)},
                )
                for r in await related_result.data():
                    add_edge(
                        f"entity:{r['source']}",
                        f"entity:{r['target']}",
                        r.get("relation") or "关联",
                        "related",
                    )

        nodes = list(node_map.values())
        edges = list(edge_map.values())
        tag_count = sum(1 for n in nodes if n["kind"] == "tag")

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "nodeCount": len(nodes),
                "edgeCount": len(edges),
                "documentCount": document_count,
                "entityCount": entity_count,
                "tagCount": tag_count,
                "mentionCount": mention_count,
                "relatedCount": related_count,
                "entityTypes": entity_types,
            },
            "topEntities": top_entities,
            "recentNodes": recent_nodes,
            "entityTypes": [t["type"] for t in entity_types],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("图谱全景查询失败：%s", e)
        return empty
