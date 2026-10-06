"""kh_user / kh_role / kh_user_role / kh_permission / kh_role_permission / kh_user_permission。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import StrBigInt
from app.db.models.document import Base


class User(Base):
    __tablename__ = "kh_user"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    real_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email_verified: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Role(Base):
    __tablename__ = "kh_role"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    role_name: Mapped[str] = mapped_column(String(50), nullable=False)
    role_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)


class UserRole(Base):
    __tablename__ = "kh_user_role"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    user_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    role_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Permission(Base):
    __tablename__ = "kh_permission"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    parent_id: Mapped[str] = mapped_column(StrBigInt, nullable=False, default="0")
    permission_name: Mapped[str] = mapped_column(String(50), nullable=False)
    permission_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    permission_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    menu_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RolePermission(Base):
    __tablename__ = "kh_role_permission"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    role_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    permission_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserPermission(Base):
    __tablename__ = "kh_user_permission"

    id: Mapped[str] = mapped_column(StrBigInt, primary_key=True, autoincrement=False)
    user_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    permission_id: Mapped[str] = mapped_column(StrBigInt, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
