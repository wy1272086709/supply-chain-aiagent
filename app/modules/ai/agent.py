"""Agent 构建 — 对应 ai-stream.service.ts 的 createTurnAgent + systemForPlan（提示词逐字）。

langchain Python 1.x 的 create_agent + SummarizationMiddleware(12/keep 6) +
ModelCallLimitMiddleware(runLimit 8, exit=end) 与 JS 版语义一致。
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.modules.ai.agentic_retrieve import retrieve_and_grade
from app.modules.ai import query_rewrite, web_search
from app.modules.documents.access import access_from_user
from app.modules.pipeline.graph_build import format_graph_context, retrieve_for_chat

logger = logging.getLogger(__name__)

EXCERPT_LEN = 200

_llm = None
_compact_llm = None
_tried = False


def _excerpt(content: str) -> str:
    text = re.sub(r"\s+", " ", content or "").strip()
    return text if len(text) <= EXCERPT_LEN else text[:EXCERPT_LEN] + "..."


def _get_llms():
    """(流式 llm, 摘要/compact llm)；未配置 Key 返回 (None, None)。"""
    global _llm, _compact_llm, _tried
    if _tried:
        return _llm, _compact_llm
    _tried = True
    cfg = get_settings()
    api_key = cfg.llm_api_key_resolved
    if not api_key:
        return None, None

    from langchain_openai import ChatOpenAI

    model_kwargs: dict = {"parallel_tool_calls": True}
    if cfg.thinking_enabled:
        model_kwargs["enable_thinking"] = True
    _llm = ChatOpenAI(
        api_key=api_key,
        model=cfg.llm_model_resolved,
        temperature=0.2,
        timeout=cfg.ai_chat_timeout_ms,
        max_retries=0,
        stream_usage=False,
        base_url=cfg.llm_base_url_resolved,
        extra_body=model_kwargs,
    )
    # 摘要/分类不要开思考，结构化输出更容易稳
    _compact_llm = ChatOpenAI(
        api_key=api_key,
        model=cfg.llm_model_resolved,
        temperature=0,
        timeout=cfg.ai_chat_timeout_ms,
        max_retries=0,
        base_url=cfg.llm_base_url_resolved,
    )
    return _llm, _compact_llm


# ---------------- 工具入参 schema ----------------


class RetrieveKnowledgeInput(BaseModel):
    query: str = Field(min_length=1, description="适合检索的关键词或短句")
    topK: int | None = Field(default=None, ge=1, le=10, description="条数，默认与请求一致")


class RewriteQueryInput(BaseModel):
    focus: str | None = Field(default=None, description="希望改写时强调的缺口，可空")


class RetrieveGraphInput(BaseModel):
    query: str = Field(min_length=1, description="短实体名，多个用空格或 / 分隔，不要整句")


class WebSearchInput(BaseModel):
    query: str = Field(min_length=1, description="搜索关键词")
    count: int | None = Field(default=None, ge=1, le=10, description="条数，默认 5")


RETRIEVE_KNOWLEDGE_DESCRIPTION = (
    "检索企业知识库一次（仅当前用户可见文档）并评估是否切题。不会自动再查。"
    "首次必须与 retrieve_graph 在同一次并行调用。eval.ok 为 false 时先 rewrite_query，"
    "再用新词再调本工具。本轮最多两次。"
)

REWRITE_QUERY_DESCRIPTION = (
    "根据上次检索评估改写检索词。仅在 retrieve_knowledge 返回 insufficient 后调用，"
    "再把返回的 query 交给 retrieve_knowledge。"
)

RETRIEVE_GRAPH_DESCRIPTION = (
    "检索知识图谱实体与关系（仅当前用户可见文档抽取出的图）。入参必须是短实体名，"
    "多个用空格或 / 分隔，例如「发票 / 报销」，不要整句。首次检索知识库时必须与 "
    "retrieve_knowledge 在同一次并行调用。图谱只说明实体关系，不能当制度原文。"
    "仅当还要查其他实体名时再单独再调。"
)

WEB_SEARCH_DESCRIPTION = (
    "联网搜索公开信息（Bocha）。仅在知识库循环结束后仍 insufficient，"
    "或本轮只需公开时效信息时调用。不要用网页替代已切题的知识库资料。"
)


def _pack_retrieve_result(hits: list[dict], query: str, persist_sources: list[dict], emitter, extra: dict = {}) -> dict:
    """命中写入 citations，推 data-sources；context 给模型，excerpt 给前端。"""
    offset = len(persist_sources)
    sources = [
        {
            "index": offset + i + 1,
            "documentId": hit["documentId"],
            "documentTitle": hit["documentTitle"],
            "heading": hit.get("heading"),
            "excerpt": _excerpt(hit.get("content") or ""),
            "score": hit.get("score", 0),
        }
        for i, hit in enumerate(hits)
    ]
    persist_sources.extend(sources)
    emitter.data("sources", persist_sources)
    for src in sources:
        emitter.source_document(src["documentId"], f"[{src['index']}] {src['documentTitle']}")
    context_blocks: list[str] = []
    for i, hit in enumerate(hits):
        src = sources[i]
        heading = f" / {hit['heading']}" if hit.get("heading") else ""
        content = hit.get("content") or ""
        snippet = content[:800] + "..." if len(content) > 800 else content
        context_blocks.append(f"[{src['index']}] {hit['documentTitle']}{heading}\n{snippet}")
    context = "\n\n".join(context_blocks)
    return {
        "query": query,
        "items": [
            {
                "index": src["index"],
                "documentId": src["documentId"],
                "documentTitle": src["documentTitle"],
                "heading": src["heading"],
                "excerpt": src["excerpt"],
            }
            for src in sources
        ],
        "context": context,
        **extra,
    }


def create_turn_agent(user: dict, top_k: int, persist_sources: list[dict], emitter, plan: dict, question: str):
    """按本轮意图挂工具；知识库与图谱都是 Agent tool，首次应并行调用。"""
    llm, compact_llm = _get_llms()
    if llm is None or compact_llm is None:
        return None

    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware, SummarizationMiddleware
    from langchain_core.tools import tool

    tools = []
    state = {"retrieve_count": 0, "last_retrieve": None}

    if plan["allowRetrieve"]:

        @tool("retrieve_knowledge", description=RETRIEVE_KNOWLEDGE_DESCRIPTION, args_schema=RetrieveKnowledgeInput)
        async def retrieve_knowledge(query: str, topK: int | None = None) -> dict:
            if state["retrieve_count"] >= 2:
                return {
                    "query": query,
                    "items": [],
                    "insufficient": True,
                    "error": "本轮知识库最多检索两次，请根据已有评估作答或联网",
                }
            state["retrieve_count"] += 1
            result = await retrieve_and_grade(
                question=question,
                query=query,
                top_k=topK if topK is not None else top_k,
                user=user,
                retried=state["retrieve_count"] > 1,
                previous_query=(state["last_retrieve"] or {}).get("usedQuery"),
            )
            state["last_retrieve"] = result
            emitter.data("eval", result["eval"])
            if result["eval"]["reason"] == "error":
                logger.warning("RAG 检索失败：%s", result["eval"]["text"])
                return {
                    "query": result["usedQuery"],
                    "items": [],
                    "error": result["eval"]["text"],
                    "insufficient": True,
                    "eval": result["eval"],
                }
            if not result["eval"]["ok"]:
                return _pack_retrieve_result(
                    [], result["usedQuery"], persist_sources, emitter,
                    {"insufficient": True, "eval": result["eval"]},
                )
            return _pack_retrieve_result(
                result["hits"], result["usedQuery"], persist_sources, emitter,
                {"eval": result["eval"]},
            )

        @tool("rewrite_query", description=REWRITE_QUERY_DESCRIPTION, args_schema=RewriteQueryInput)
        async def rewrite_query(focus: str | None = None) -> dict:
            last = state["last_retrieve"]
            if not last:
                return {"error": "请先调用 retrieve_knowledge"}
            if last["eval"]["ok"]:
                return {"error": "当前资料已切题，无需改写", "query": last["usedQuery"]}
            emitter.data("status", {"stage": "rewrite", "text": "正在改写检索词…"})
            retry_query = await query_rewrite.rewrite_after_retrieve(
                question, last["usedQuery"], last["grade"], last["rawHits"]
            )
            if not retry_query:
                return {
                    "error": "无法改写出与上次不同的检索词",
                    "previousQuery": last["usedQuery"],
                    "reason": last["eval"]["text"],
                }
            return {
                "query": retry_query,
                "previousQuery": last["usedQuery"],
                "reason": last["eval"]["text"],
                "focus": focus,
            }

        tools.extend([retrieve_knowledge, rewrite_query])

    if plan["allowGraph"]:

        @tool("retrieve_graph", description=RETRIEVE_GRAPH_DESCRIPTION, args_schema=RetrieveGraphInput)
        async def retrieve_graph(query: str) -> dict:
            keywords = [*plan["graphQueries"]]
            trimmed = (query or "").strip()
            if trimmed:
                keywords.append(trimmed)
            hit = await retrieve_for_chat(keywords, 8, access_from_user(user))
            emitter.data("graph", hit)
            return {
                "query": hit["query"],
                "entities": hit["entities"],
                "relations": hit["relations"],
                "documents": hit["documents"],
                "context": format_graph_context(hit),
                "insufficient": not hit["entities"] and not hit["relations"],
            }

        tools.append(retrieve_graph)

    if plan["allowWeb"]:

        @tool("web_search", description=WEB_SEARCH_DESCRIPTION, args_schema=WebSearchInput)
        async def web_search_tool(query: str, count: int | None = None) -> dict:
            return await web_search.search(query, count if count is not None else 5)

        tools.append(web_search_tool)

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_for_plan(plan),
        middleware=[
            SummarizationMiddleware(
                model=compact_llm,
                trigger=("messages", 12),
                keep=("messages", 6),
                summary_prompt=(
                    "用中文简洁总结对话：话题、已确认结论、待办。不要写入知识库条文。\n\n待摘要的对话：\n{messages}\n\n摘要："
                ),
            ),
            ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
        ],
    )


def system_for_plan(plan: dict) -> str:
    """Agent 循环系统提示词 — 逐字移植 systemForPlan。"""
    prompt = (
        "你是企业知识库助手，必须按 Agentic RAG 循环作答，不要跳步。"
        "结合对话历史和记忆里的用户背景，但制度/流程以本轮检索资料为准。"
        f"本轮意图：{plan['label']}。"
    )
    if plan["allowRetrieve"] and plan["allowGraph"]:
        graph_hint = " / ".join(plan["graphQueries"]) if plan["graphQueries"] else "从问题抽出的短实体名"
        prompt += (
            f"知识库建议检索词「{plan['query']}」，图谱建议检索词「{graph_hint}」。"
            "第一步必须在同一次回复里并行调用 retrieve_knowledge 和 retrieve_graph，两个都要调："
            "不要先查库再决定是否查图，不要只调其中一个，也不要等一个返回再调另一个。"
            "retrieve_graph 的 query 必须是短实体名（空格或 / 分隔），不要整句。"
            "循环：① 并行检索；② 若知识库 eval.ok 则用文档 context 作答并标 [n]，图谱只看实体关系、不能当制度原文；"
            "③ 若 insufficient，先 rewrite_query，再用新词再次 retrieve_knowledge（知识库最多两次）；图谱词变了才再调 retrieve_graph；"
            "④ 两次后仍不足再考虑联网。eval.ok 后不要再检索、不要改写。不要第三次 retrieve_knowledge。"
            "图谱为空不要编造关系。不要用记忆替代文档。"
        )
    elif plan["allowRetrieve"]:
        prompt += (
            f"建议检索词「{plan['query']}」，可改写成更准的 query。"
            "循环：① retrieve_knowledge；② 若 eval.ok 则用 context 作答并标 [n]；"
            "③ 若 insufficient，先 rewrite_query，再用返回的新词再次 retrieve_knowledge（知识库最多两次）；"
            "④ 两次后仍不足再考虑联网。eval.ok 后不要再检索、不要改写。不要第三次 retrieve_knowledge。"
            "不要用记忆替代文档。"
        )
    else:
        prompt += "不要调用 retrieve_knowledge 或 rewrite_query。"
    if not plan["allowGraph"]:
        prompt += "不要调用 retrieve_graph。"
    if plan["intent"] == "chitchat":
        prompt += "这是闲聊，直接回应，不要调用任何工具。"
    if plan["intent"] == "profile":
        prompt += "这是个人偏好或身份问题，根据记忆回答，不要检索知识库。"
    if plan["intent"] == "kb":
        prompt += "两次检索后仍 insufficient：明确说知识库没有相关内容，禁止编造制度，不要联网。"
    if plan["intent"] == "web":
        prompt += "用 web_search 查公开信息，用标题+链接说明，不要编造。"
    if plan["intent"] == "kb_then_web":
        prompt += "先走完知识库循环；两次后仍 insufficient 再 web_search。联网结果用标题+链接说明，不要把网页写成内部制度。"
    if not plan["allowWeb"]:
        prompt += "不要调用 web_search。"
    prompt += "资料不够就明确说不知道。"
    return prompt
