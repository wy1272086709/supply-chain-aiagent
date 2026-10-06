"""kh_team / kh_team_member。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import StrBigInt
from app.db.models.document import Base


class Team(Base):
    __tablename__ = "kh_team"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    team_name: Mapped[str] = mapped_column(String(100), nullable=False)
    team_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    leader_id: Mapped[str | None] = mapped_column(StrBigInt, nullable=True)
    parent_id: Mapped[str] = mapped_column(StrBigInt, nullable=False, default="0")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TeamMember(Base):
    __tablename__ = "kh_team_member"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    team_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    user_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    member_role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
