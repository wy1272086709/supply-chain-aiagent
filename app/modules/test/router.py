"""团队路由 — 对应 src/team/team.controller.ts。

成员增删的 body 是裸 JSON 数组（非对象），用 Request 手动解析。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, HTTPException

router = APIRouter(prefix="/test", tags=[])

@router.get("/test1")
def test1():
    return [{"name": 'wangyu'}]

@router.get("/test2")
def test2():
    raise Exception()