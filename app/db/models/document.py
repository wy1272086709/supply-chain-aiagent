"""kh_document / kh_document_review — 列与 init.sql 逐字对齐。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, SmallInteger, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import StrBigInt


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "kh_document"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    category_id: Mapped[str | None] = mapped_column(StrBigInt, nullable=True)
    team_id: Mapped[str | None] = mapped_column(StrBigInt, nullable=True)
    author_id: Mapped[str | None] = mapped_column(StrBigInt, nullable=True)
    cover_image: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    remark: Mapped[str | None] = mapped_column(String, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    favourite_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    create_by: Mapped[str | None] = mapped_column(StrBigInt, nullable=True)
    update_by: Mapped[str | None] = mapped_column(StrBigInt, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DocumentReview(Base):
    __tablename__ = "kh_document_review"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    document_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(StrBigInt, nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    review_result: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    review_comment: Mapped[str | None] = mapped_column(String, nullable=True)
    before_status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
