"""会话服务 — 对应 chat-session.service.ts（kh_ai_session / kh_ai_message）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.snowflake import next_snowflake_id
from app.common.serialization import iso_z
from app.db.models import AiMessage, AiSession
from app.modules.ai.types import title_from_question
from app.common.now import utcnow

DEFAULT_TITLE = "新对话"


def session_vo(session: AiSession) -> dict:
    return {
        "id": session.id,
        "userId": session.user_id,
        "title": session.title,
        "createdAt": iso_z(session.created_at),
        "updatedAt": iso_z(session.updated_at),
    }


def message_vo(message: AiMessage) -> dict:
    return {
        "id": message.id,
        "sessionId": message.session_id,
        "role": message.role,
        "content": message.content,
        "sources": message.sources,
        "createdAt": iso_z(message.created_at),
    }


async def page_mine(db: AsyncSession, user_id: str, query: dict) -> dict:
    page = query.get("page") or 1
    page_size = query.get("pageSize") or 20
    base = select(AiSession).where(AiSession.user_id == user_id)
    count_q = select(func.count()).select_from(AiSession).where(AiSession.user_id == user_id)
    total = (await db.execute(count_q)).scalar_one()
    sessions = (
        (
            await db.execute(
                base.order_by(AiSession.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [session_vo(s) for s in sessions], "total": total, "page": page, "pageSize": page_size}


async def create(db: AsyncSession, user_id: str, title: str | None = None) -> AiSession:
    now = utcnow()
    session = AiSession(
        id=next_snowflake_id(),
        user_id=user_id,
        title=(title or "").strip()[:80] or DEFAULT_TITLE,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    await db.commit()
    return session


async def rename(db: AsyncSession, user_id: str, session_id: str, title: str) -> dict:
    session = await _get_owned(db, user_id, session_id)
    session.title = title.strip()[:80]
    session.updated_at = utcnow()
    await db.commit()
    return session_vo(session)


async def touch_title(db: AsyncSession, user_id: str, session_id: str, question: str) -> None:
    """仍为默认标题时，用首问覆盖。"""
    session = await _get_owned(db, user_id, session_id)
    if session.title == DEFAULT_TITLE:
        session.title = title_from_question(question)
    session.updated_at = utcnow()
    await db.commit()


async def remove(db: AsyncSession, user_id: str, session_id: str) -> dict:
    await _get_owned(db, user_id, session_id)
    await db.execute(sa_delete(AiMessage).where(AiMessage.session_id == session_id))
    await db.execute(sa_delete(AiSession).where(AiSession.id == session_id))
    await db.commit()

    from app.modules.ai import long_memory, short_memory

    await short_memory.clear(user_id, session_id)
    await long_memory.clear_session(user_id, session_id)
    return {"message": "已删除"}


async def list_messages(db: AsyncSession, user_id: str, session_id: str) -> list[dict]:
    await _get_owned(db, user_id, session_id)
    messages = (
        (
            await db.execute(
                select(AiMessage)
                .where(AiMessage.session_id == session_id)
                .order_by(AiMessage.created_at.asc(), AiMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [message_vo(m) for m in messages]


async def list_recent_messages(db: AsyncSession, user_id: str, session_id: str, limit: int):
    """最近 N 条，时间正序，供 Redis miss 时回填工作窗口。"""
    await _get_owned(db, user_id, session_id)
    rows = (
        (
            await db.execute(
                select(AiMessage)
                .where(AiMessage.session_id == session_id)
                .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
                .limit(max(limit, 1))
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


async def append_turn(
    db: AsyncSession,
    user_id: str,
    session_id: str | None,
    question: str,
    answer: str,
    sources: list[dict],
) -> AiSession:
    """问答落库：无 sessionId 则新建；标题在仍为默认名时用首问覆盖。"""
    if session_id:
        session = await _get_owned(db, user_id, session_id)
    else:
        session = await create(db, user_id, title_from_question(question))

    if session.title == DEFAULT_TITLE:
        session.title = title_from_question(question)
    session.updated_at = utcnow()
    await db.commit()

    now = utcnow()
    db.add(AiMessage(id=next_snowflake_id(), session_id=session.id, role="user",
                     content=question, created_at=now))
    db.add(AiMessage(id=next_snowflake_id(), session_id=session.id, role="assistant",
                     content=answer, sources=sources if sources else None, created_at=now))
    await db.commit()
    return session


async def _get_owned(db: AsyncSession, user_id: str, session_id: str) -> AiSession:
    session = (
        await db.execute(
            select(AiSession).where(AiSession.id == session_id, AiSession.user_id == user_id)
        )
    ).scalar_one_or_none()
    if not session:
        raise NotFoundError("会话不存在")
    return session
