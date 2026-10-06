"""权限 DTO — 对应 src/user/dto/permission.dto.ts。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreatePermissionDto(BaseModel):
    model_config = {"extra": "forbid"}

    permissionName: str
    permissionCode: str
    permissionType: int
    parentId: str | None = None
    menuUrl: str | None = None
    apiUrl: str | None = None
    method: str | None = None
    icon: str | None = None
    sort: int | None = None
    status: int | None = None


class UpdatePermissionDto(BaseModel):
    model_config = {"extra": "forbid"}

    permissionName: str | None = None
    permissionCode: str | None = None
    permissionType: int | None = None
    parentId: str | None = None
    menuUrl: str | None = None
    apiUrl: str | None = None
    method: str | None = None
    icon: str | None = None
    sort: int | None = None
    status: int | None = None


class QueryPermissionDto(BaseModel):
    model_config = {"extra": "forbid"}

    keyword: str | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


class AssignPermissionIdsDto(BaseModel):
    model_config = {"extra": "forbid"}

    permissionIds: list[str]
