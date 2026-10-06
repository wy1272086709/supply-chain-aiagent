"""Bocha 联网搜索 — 对应 web-search.service.ts。"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

API_URL = "https://api.bochaai.com/v1/web-search"


async def search(query: str, count: int = 5) -> dict:
    cfg = get_settings()
    if not cfg.bocha_api_key:
        return {"query": query, "items": [], "error": "未配置 BOCHA_API_KEY，无法联网搜索"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {cfg.bocha_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "freshness": "noLimit",
                    "summary": True,
                    "count": max(1, min(count, 10)),
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Bocha 搜索异常：%s", e)
        return {"query": query, "items": [], "error": f"搜索失败（{e}）"}

    if response.status_code != 200:
        logger.warning("Bocha 搜索失败：status=%d", response.status_code)
        return {
            "query": query,
            "items": [],
            "error": f"搜索失败（{response.status_code}）{response.text[:120]}",
        }

    body = response.json()
    if body.get("code") != 200 or not body.get("data"):
        return {"query": query, "items": [], "error": body.get("msg") or "搜索接口返回异常"}

    pages = ((body.get("data") or {}).get("webPages") or {}).get("value") or []
    items = [
        {
            "title": p.get("name") or "",
            "url": p.get("url") or "",
            "snippet": (p.get("summary") or p.get("snippet") or "").strip(),
            "siteName": p.get("siteName"),
        }
        for p in pages
        if p.get("url") and p.get("name")
    ]
    return {"query": query, "items": items}
