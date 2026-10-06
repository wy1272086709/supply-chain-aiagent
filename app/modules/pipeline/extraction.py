"""实体/关系抽取 — 对应 extraction.service.ts（with_structured_output → pydantic）。"""

from __future__ import annotations

import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import get_settings
from app.modules.pipeline.kg_schema import (
    KgExtractionResult,
    build_extraction_system_prompt,
    normalize_entity_type,
    normalize_relation_type,
)

logger = logging.getLogger(__name__)

_structured_llm = None
_tried = False


def _max_entities() -> int:
    return get_settings().kg_max_entities


def _max_relations() -> int:
    return get_settings().kg_max_relations


def _get_structured_llm():
    global _structured_llm, _tried
    if _tried:
        return _structured_llm
    _tried = True
    cfg = get_settings()
    api_key = cfg.llm_api_key_resolved
    if not api_key:
        return None

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        api_key=api_key,
        model=cfg.llm_model_resolved,
        temperature=0.1,
        timeout=cfg.kg_llm_timeout_ms,
        max_retries=0,
        base_url=cfg.llm_base_url_resolved,
    )
    _structured_llm = llm.with_structured_output(
        KgExtractionResult, method="function_calling"
    )
    return _structured_llm


async def extract(content: str, heading: str | None, document_title: str) -> dict:
    if not (content or "").strip():
        return {"entities": [], "relations": []}
    return await _extract_by_llm(content, heading, document_title)


async def _extract_by_llm(content: str, heading: str | None, document_title: str) -> dict:
    structured = _get_structured_llm()
    if structured is None:
        raise RuntimeError(
            "KG 抽取未配置 API Key（OPENAI_API_KEY / LLM_API_KEY / DASHSCOPE_API_KEY）"
        )

    system = build_extraction_system_prompt(_max_entities(), _max_relations())
    user = f"文档标题: {document_title}\n章节: {heading or '无'}\n\n内容:\n{content[:4000]}"

    started = time.monotonic()
    parsed = await structured.ainvoke([SystemMessage(system), HumanMessage(user)])
    elapsed = int((time.monotonic() - started) * 1000)
    logger.info(
        "KG 抽取完成：title=%s, elapsed=%dms, chars=%d, entities=%d",
        document_title, elapsed, len(content), len(parsed.entities or []),
    )
    return _to_extraction_result(parsed)


def _to_extraction_result(parsed: KgExtractionResult) -> dict:
    """截断数量、规范化类型、丢掉挂空实体的关系。"""
    entity_names: set[str] = set()
    entities: list[dict] = []
    for e in (parsed.entities or [])[: _max_entities()]:
        name = (e.name or "").strip()
        if not name:
            continue
        entity_names.add(name)
        entities.append(
            {
                "name": name,
                "type": normalize_entity_type(e.type),
                "description": (e.description or "").strip(),
                "aliases": [str(a).strip() for a in (e.aliases or []) if str(a).strip()],
            }
        )

    relations: list[dict] = []
    for r in (parsed.relations or [])[: _max_relations()]:
        source = (r.source or "").strip()
        target = (r.target or "").strip()
        if not source or not target or source not in entity_names or target not in entity_names:
            continue
        relations.append(
            {
                "source": source,
                "target": target,
                "relation": normalize_relation_type(r.relation or r.type),
                "weight": r.weight if isinstance(r.weight, (int, float)) else 0.5,
            }
        )
    return {"entities": entities, "relations": relations}
