"""kh_ai_session / kh_ai_message。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import StrBigInt
from app.db.models.document import Base


class AiSession(Base):
    __tablename__ = "kh_ai_session"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    user_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AiMessage(Base):
    __tablename__ = "kh_ai_message"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    session_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JSONB().with_variant(JSON, "sqlite"), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
