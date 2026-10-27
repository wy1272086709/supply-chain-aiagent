"""单次检索 + 切题评估 / 无 Agent 等价环 — 对应 agentic-retrieve.ts。"""

from __future__ import annotations

from app.modules.ai import hybrid_retrieval, query_rewrite

EMPTY_GRADE = {"ok": False, "reason": "empty", "text": "未检索到资料"}


def _to_eval(grade: dict, query: str, retried: bool, retry_query: str | None) -> dict:
    if grade["reason"] == "relevant":
        return {
            "ok": True,
            "reason": "retried_ok" if retried else "ok",
            "text": f"已改写再查，{grade['text']}" if retried else grade["text"],
            "retried": retried,
            "query": query,
            "retryQuery": retry_query,
        }
    if grade["reason"] == "empty":
        return {
            "ok": False,
            "reason": "retried_empty" if retried else "empty",
            "text": "改写后仍无结果" if retried else grade["text"],
            "retried": retried,
            "query": query,
            "retryQuery": retry_query,
        }
    return {
        "ok": False,
        "reason": "retried_irrelevant" if retried else "irrelevant",
        "text": f"改写后仍不切题：{grade['text']}" if retried else grade["text"],
        "retried": retried,
        "query": query,
        "retryQuery": retry_query,
    }


async def retrieve_and_grade(
    *,
    question: str,
    query: str,
    top_k: int,
    user: dict | None = None,
    retried: bool = False,
    previous_query: str | None = None,
) -> dict:
    """单次检索 + 切题评估。Agent loop 每次工具调用走这里。"""
    used_query = (query or "").strip() or question
    display_query = (previous_query or used_query) if retried else used_query
    retry_query = used_query if retried else None

    try:
        raw_hits = await hybrid_retrieval.retrieve(used_query, top_k, user)
        grade = await query_rewrite.grade_hits(question, raw_hits)
        return {
            "rawHits": raw_hits,
            "hits": raw_hits if grade["ok"] else [],
            "grade": grade,
            "eval": _to_eval(grade, display_query, retried, retry_query),
            "usedQuery": used_query,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "rawHits": [],
            "hits": [],
            "grade": {**EMPTY_GRADE, "text": f"检索失败：{e}"},
            "eval": {
                "ok": False,
                "reason": "error",
                "text": "改写后再次检索失败" if retried else "检索失败",
                "retried": retried,
                "query": display_query,
                "retryQuery": retry_query,
            },
            "usedQuery": used_query,
        }


async def retrieve_until_relevant(
    *,
    question: str,
    query: str,
    top_k: int,
    user: dict | None = None,
    on_eval=None,
    on_rewrite=None,
) -> dict:
    """无 Agent 时的等价环：查一次 → 评估 → 不足则改写再查一次。"""
    first = await retrieve_and_grade(
        question=question, query=query, top_k=top_k, user=user
    )
    if on_eval:
        await on_eval(first["eval"])
    if first["eval"]["ok"] or first["eval"]["reason"] == "error":
        return {
            "hits": first["hits"],
            "eval": first["eval"],
            "usedQuery": first["usedQuery"],
            "rawHits": first["rawHits"],
        }

    retry_query = await query_rewrite.rewrite_after_retrieve(
        question, first["usedQuery"], first["grade"], first["rawHits"]
    )
    if not retry_query:
        return {
            "hits": [],
            "eval": first["eval"],
            "usedQuery": first["usedQuery"],
            "rawHits": first["rawHits"],
        }

    if on_rewrite:
        await on_rewrite(retry_query)
    second = await retrieve_and_grade(
        question=question,
        query=retry_query,
        top_k=top_k,
        user=user,
        retried=True,
        previous_query=first["usedQuery"],
    )
    if on_eval:
        await on_eval(second["eval"])
    return {
        "hits": second["hits"],
        "eval": second["eval"],
        "usedQuery": second["usedQuery"],
        "rawHits": second["rawHits"],
    }
