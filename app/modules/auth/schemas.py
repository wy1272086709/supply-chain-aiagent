"""Auth / 用户 / 角色 / 团队相关 DTO — 对应 src/auth/dto、src/user/dto、src/team/dto。

extra='forbid' 对应 Nest ValidationPipe forbidNonWhitelisted。
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginDto(BaseModel):
    model_config = {"extra": "forbid"}

    username: str
    password: str


class RegisterDto(BaseModel):
    model_config = {"extra": "forbid"}

    username: str
    password: str = Field(min_length=6)
    email: EmailStr | None = None
    realName: str | None = None


class RefreshTokenDto(BaseModel):
    model_config = {"extra": "forbid"}

    refreshToken: str


class SendResetCodeDto(BaseModel):
    model_config = {"extra": "forbid"}

    email: EmailStr


class ResetPasswordByEmailDto(BaseModel):
    model_config = {"extra": "forbid"}

    email: EmailStr
    code: str
    newPassword: str = Field(min_length=6)


# ---------------- 用户管理 ----------------


class CreateUserDto(BaseModel):
    model_config = {"extra": "forbid"}

    username: str
    password: str = Field(min_length=6)
    email: EmailStr | None = None
    realName: str | None = None
    avatar: str | None = None
    status: int | None = None
    roleCodes: list[str] | None = None


class UpdateUserDto(BaseModel):
    model_config = {"extra": "forbid"}

    email: EmailStr | None = None
    realName: str | None = None
    avatar: str | None = None
    status: int | None = None


class UpdateProfileDto(BaseModel):
    model_config = {"extra": "forbid"}

    email: EmailStr | None = None
    realName: str | None = None
    avatar: str | None = None


class ChangePasswordDto(BaseModel):
    model_config = {"extra": "forbid"}

    oldPassword: str
    newPassword: str = Field(min_length=6)


class ResetPasswordDto(BaseModel):
    model_config = {"extra": "forbid"}

    newPassword: str = Field(min_length=6)


class QueryUserDto(BaseModel):
    model_config = {"extra": "forbid"}

    keyword: str | None = None
    roleCode: str | None = None
    status: int | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class AssignRolesDto(BaseModel):
    model_config = {"extra": "forbid"}

    roleCodes: list[str] = Field(min_length=1)


class AssignPermissionIdsDto(BaseModel):
    model_config = {"extra": "forbid"}

    permissionIds: list[str]


# ---------------- 角色 ----------------


class CreateRoleDto(BaseModel):
    model_config = {"extra": "forbid"}

    roleName: str
    roleCode: str
    description: str | None = None


class UpdateRoleDto(BaseModel):
    model_config = {"extra": "forbid"}

    roleName: str | None = None
    description: str | None = None
    status: int | None = None


# ---------------- 团队 ----------------


class CreateTeamDto(BaseModel):
    model_config = {"extra": "forbid"}

    teamName: str
    teamCode: str | None = None
    description: str | None = None
    leaderId: str | None = None
    parentId: str | None = None
    sort: int | None = None
    status: int | None = None


class UpdateTeamDto(BaseModel):
    model_config = {"extra": "forbid"}

    teamName: str | None = None
    teamCode: str | None = None
    description: str | None = None
    leaderId: str | None = None
    parentId: str | None = None
    sort: int | None = None
    status: int | None = None


class QueryTeamDto(BaseModel):
    model_config = {"extra": "forbid"}

    keyword: str | None = None
    status: int | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)
