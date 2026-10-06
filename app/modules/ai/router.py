"""AI 路由 — 对应 src/ai/ai.controller.ts。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.common.constants import PERMISSION_SEARCH
from app.modules.ai import chat_service, hybrid_retrieval, sessions, stream_service
from app.modules.ai.schemas import (
    ChatDto,
    ChatStreamDto,
    CreateSessionDto,
    QuerySessionDto,
    RagSearchDto,
    UpdateSessionDto,
)
from app.modules.ai.ui_stream import UI_MESSAGE_STREAM_HEADERS
from app.modules.auth.deps import CurrentUser, DbSession, require_permissions

router = APIRouter(tags=["ai"])

SearchPerm = Depends(require_permissions(PERMISSION_SEARCH))


@router.post("/rag/search")
async def rag_search(dto: RagSearchDto, user: CurrentUser, _: object = SearchPerm):
    """RAG 混合检索：BM25 + kNN → RRF → rerank；不调用 LLM。"""
    return await hybrid_retrieval.retrieve(dto.query.strip(), dto.topK or 5, user)


@router.post("/ai/chat")
async def chat(dto: ChatDto, user: CurrentUser, _: object = SearchPerm):
    return await chat_service.chat(dto.content, dto.topK or 5, user, dto.sessionId)


@router.post("/ai/chat/stream")
async def chat_stream(dto: ChatStreamDto, user: CurrentUser, _: object = SearchPerm):
    """Agentic RAG 流式作答（UIMessageStream over SSE）。"""
    return StreamingResponse(
        stream_service.stream_chat(dto, user),
        headers=UI_MESSAGE_STREAM_HEADERS,
        media_type="text/event-stream",
    )


@router.get("/ai/sessions")
async def list_sessions(query: Annotated[QuerySessionDto, Query()], user: CurrentUser, db: DbSession, _: object = SearchPerm):
    return await sessions.page_mine(db, user["userId"], query.model_dump())


@router.post("/ai/sessions")
async def create_session(dto: CreateSessionDto, user: CurrentUser, db: DbSession, _: object = SearchPerm):
    session = await sessions.create(db, user["userId"], dto.title)
    from app.modules.ai.sessions import session_vo

    return session_vo(session)


@router.get("/ai/sessions/{session_id}/messages")
async def list_messages(session_id: str, user: CurrentUser, db: DbSession, _: object = SearchPerm):
    return await sessions.list_messages(db, user["userId"], session_id)


@router.patch("/ai/sessions/{session_id}")
async def rename_session(session_id: str, dto: UpdateSessionDto, user: CurrentUser, db: DbSession, _: object = SearchPerm):
    return await sessions.rename(db, user["userId"], session_id, dto.title)


@router.delete("/ai/sessions/{session_id}")
async def remove_session(session_id: str, user: CurrentUser, db: DbSession, _: object = SearchPerm):
    return await sessions.remove(db, user["userId"], session_id)
