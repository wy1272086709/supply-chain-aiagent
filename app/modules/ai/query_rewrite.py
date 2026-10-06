"""对话路由/检索改写 — 对应 chat-query-rewrite.service.ts，提示词逐字移植。"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.modules.ai.memory_util import compact_rewrite_context

logger = logging.getLogger(__name__)

CHAT_INTENTS = ["chitchat", "profile", "kb", "web", "kb_then_web"]

INTENT_LABEL = {
    "chitchat": "闲聊",
    "profile": "个人偏好",
    "kb": "知识库",
    "web": "联网搜索",
    "kb_then_web": "知识库，不足则联网",
}

GRADE_PROMPT = (
    "你是企业知识库的检索评估器。判断命中资料能否回答用户问题。\n"
    "\n"
    "- 切题：同一主题、同一制度/岗位/流程，能支撑作答（不必覆盖每个细节）\n"
    "- 不切题：只是词沾边（如问加班餐补却命中差旅报销）、或完全另一件事\n"
    "- 只根据给定标题和摘录判断，不要假设库里还有别的文档\n"
    "- 输出不要解释字段以外的内容"
)

RETRY_REWRITE_PROMPT = (
    "你是企业知识库的检索改写器。上次检索不足，请换一种问法再查。\n"
    "\n"
    "- 紧扣用户原问题，不要跑题\n"
    "- 不要重复上次检索词\n"
    "- 可换同义、补全制度/岗位/补贴类型等核心实体\n"
    "- 不要编造条款号、专有名词\n"
    "- 一句中文，尽量不超过 40 字"
)

ROUTE_PROMPT = (
    "你是企业知识库的意图与检索改写器。根据对话判断本轮意图，给出文档检索句，以及图谱要用的短实体名。\n"
    "\n"
    "## intent\n"
    "- chitchat：问好、致谢、与库无关的闲聊\n"
    "- profile：只问/改「我自己」的名字、我所在部门、回答长短偏好。主语必须是用户本人\n"
    "- kb：公司制度、流程、岗位、内部系统、文档\n"
    "- web：只要最新公开信息，明显不是内部制度\n"
    "- kb_then_web：像内部问题但也可能要外部时效（政策新闻+内部流程）\n"
    "\n"
    "## 易混\n"
    "- 「我是哪个部门 / 以后回答短一点」→ profile\n"
    "- 「预算审核员属于哪个部门」→ kb，不要因为出现「部门」就判 profile\n"
    "- 记忆里的用户部门不能改变本题意图\n"
    "\n"
    "## standalone_query\n"
    "- 给知识库全文检索用，可以是一句；消解「这个 / 谁负责 / 怎么办」，尽量不超过 40 字\n"
    "- 不要编造上文没有的专有名词、条款号\n"
    "- 不要复述助手已给出的制度条文\n"
    "- 闲聊/偏好：用原问题即可\n"
    "\n"
    "## graph_queries\n"
    "- 只在 kb / kb_then_web 填写，其他意图必须空数组\n"
    "- 每个词 2～4 个字的实体短名（人/岗/制度对象），不要整句、不要问号\n"
    "- 必须拆开：发票如何报销 → 发票、报销；发票报销流程和要求 → 发票、报销\n"
    "- 可补 1 个同域短名（报销可带差旅）\n"
    "- 禁止：流程、要求、办法、如何、怎么、什么、是什么\n"
    "- 输出不要解释"
)


class RouteResult(BaseModel):
    intent: str = Field(
        description="chitchat=寒暄闲聊；profile=只改用户自己的名字/偏好；kb=制度流程岗位系统；web=只要公开时效信息；kb_then_web=先查库不够再搜网"
    )
    standalone_query: str = Field(description="可独立检索的一句中文短查询，消解指代。闲聊/偏好可原样")
    graph_queries: list[str] | None = Field(
        default=None, description="图谱短实体名：2～6 个、每个 2～8 字。闲聊/偏好/纯联网必须空数组"
    )


class GradeResult(BaseModel):
    relevant: bool = Field(
        description="只要有能支撑作答的资料即为 true；仅关键词沾边、主题不是同一件事为 false"
    )
    reason: str = Field(description="一句中文：为何切题或不切题")


class RetryRewriteResult(BaseModel):
    query: str = Field(description="与上次不同的一句中文检索词，不超过 40 字，针对用户原问题")


_runners: dict[str, object] = {}
_tried = False


def _get_llm():
    cfg = get_settings()
    api_key = cfg.llm_api_key_resolved
    if not api_key:
        return None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=api_key,
        model=cfg.llm_model_resolved,
        temperature=0,
        timeout=cfg.ai_query_rewrite_timeout_ms,
        max_retries=0,
        streaming=False,
        base_url=cfg.llm_base_url_resolved,
    )


def _get_runners() -> dict:
    """router / grader / retryWriter 三个结构化输出 runner。"""
    global _tried
    if _tried:
        return _runners
    _tried = True
    llm = _get_llm()
    if llm is None:
        return _runners
    _runners["router"] = llm.with_structured_output(RouteResult, method="function_calling")
    _runners["grader"] = llm.with_structured_output(GradeResult, method="function_calling")
    _runners["retryWriter"] = llm.with_structured_output(RetryRewriteResult, method="function_calling")
    return _runners


def to_plan(intent: str, query: str, graph_queries: list[str] | None = None) -> dict:
    """意图 → 工具开关。"""
    kb = intent in ("kb", "kb_then_web")
    return {
        "intent": intent,
        "label": INTENT_LABEL.get(intent, intent),
        "query": query,
        "graphQueries": [q.strip() for q in (graph_queries or []) if q.strip()] if kb else [],
        "allowRetrieve": kb,
        "allowGraph": kb,
        "allowWeb": intent in ("web", "kb_then_web"),
    }


def _summarize_hits(hits: list[dict], limit: int = 5) -> str:
    lines = []
    for i, hit in enumerate(hits[:limit]):
        heading = f" / {hit['heading']}" if hit.get("heading") else ""
        snippet = re.sub(r"\s+", " ", hit.get("content") or "").strip()[:180]
        score = f"{hit['score']:.2f}" if isinstance(hit.get("score"), (int, float)) else "-"
        lines.append(f"{i + 1}. {hit.get('documentTitle')}{heading}（分={score}）\n{snippet}")
    return "\n".join(lines)


def _distinct_query(next_: str, previous: str) -> str:
    query = re.sub(r"\s+", " ", next_ or "").strip()
    if not query:
        return ""
    if query == re.sub(r"\s+", " ", previous or "").strip():
        return ""
    return query


async def classify(question: str, history: list[BaseMessage]) -> dict:
    """失败时按 kb 处理，宁可多检索也不漏内部制度。"""
    fallback = to_plan("kb", question)
    router = _get_runners().get("router")
    if router is None:
        return fallback

    context = compact_rewrite_context(history)
    try:
        result = await router.ainvoke(
            [
                SystemMessage(ROUTE_PROMPT),
                HumanMessage(
                    f"对话：\n{context}\n\n当前问题：{question}" if context else f"当前问题：{question}"
                ),
            ]
        )
        query = (result.standalone_query or "").strip() or question
        plan = to_plan(result.intent, query, result.graph_queries)
        logger.info(
            "意图：%s query=%s graph=%s", plan["intent"], plan["query"][:80], "/".join(plan["graphQueries"]) or "-"
        )
        return plan
    except Exception as e:  # noqa: BLE001
        logger.warning("意图识别失败，按知识库处理：%s", e)
        return fallback


async def rewrite(question: str, history: list[BaseMessage]) -> dict:
    """给非流式 /ai/chat 用：只关心要不要查知识库。"""
    plan = await classify(question, history)
    return {"query": plan["query"], "needRetrieve": plan["allowRetrieve"]}


async def grade_hits(question: str, hits: list[dict]) -> dict:
    """有命中再判切题；空结果不调模型。评估失败时有召回则放行。"""
    if not hits:
        return {"ok": False, "reason": "empty", "text": "未检索到资料"}
    grader = _get_runners().get("grader")
    if grader is None:
        return {"ok": True, "reason": "relevant", "text": "有召回（未配置评估模型）"}
    try:
        result = await grader.ainvoke(
            [
                SystemMessage(GRADE_PROMPT),
                HumanMessage(f"用户问题：{question}\n\n命中资料：\n{_summarize_hits(hits)}"),
            ]
        )
        ok = result.relevant
        text = result.reason.strip() or ("资料切题" if ok else "资料不切题")
        logger.info("检索评估：%s %s", "切题" if ok else "不切题", text[:80])
        return {"ok": ok, "reason": "relevant" if ok else "irrelevant", "text": text}
    except Exception as e:  # noqa: BLE001
        logger.warning("检索评估失败，按有召回放行：%s", e)
        return {"ok": True, "reason": "relevant", "text": "评估失败，按有召回处理"}


async def rewrite_after_retrieve(
    question: str, previous_query: str, grade: dict, hits: list[dict]
) -> str:
    """首轮空或不切题后改写。失败或与上次相同则回退原问题；原问题也相同返回空串。"""
    fallback = _distinct_query(question, previous_query)
    retry_writer = _get_runners().get("retryWriter")
    if retry_writer is None:
        return fallback
    try:
        hit_block = (
            f"\n上次命中（不切题）：\n{_summarize_hits(hits, 3)}" if hits else "\n上次无命中。"
        )
        result = await retry_writer.ainvoke(
            [
                SystemMessage(RETRY_REWRITE_PROMPT),
                HumanMessage(
                    f"用户问题：{question}\n上次检索词：{previous_query}\n不足原因：{grade['text']}{hit_block}"
                ),
            ]
        )
        query = _distinct_query(result.query.strip(), previous_query)
        if query:
            logger.info("检索改写：%s → %s", previous_query[:40], query[:40])
            return query
        return fallback
    except Exception as e:  # noqa: BLE001
        logger.warning("检索改写失败，回退原问题：%s", e)
        return fallback
