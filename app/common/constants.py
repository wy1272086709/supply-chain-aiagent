"""角色 / 权限常量 — 逐字对应 src/common/constants/{roles,permissions}.ts。"""

from __future__ import annotations

ROLE_ADMIN = "ROLE_ADMIN"
ROLE_REVIEWER = "ROLE_REVIEWER"
ROLE_USER = "ROLE_USER"

ROLE_CODE = {
    "ADMIN": ROLE_ADMIN,
    "REVIEWER": ROLE_REVIEWER,
    "USER": ROLE_USER,
}

ADMIN_ROLES = [ROLE_ADMIN]

PERMISSION_DOCUMENT_LIST = "document:list"
PERMISSION_DOCUMENT_CREATE = "document:create"
PERMISSION_DOCUMENT_EDIT = "document:edit"
PERMISSION_DOCUMENT_DELETE = "document:delete"
PERMISSION_DOCUMENT_REVIEW = "document:review"
PERMISSION_SEARCH = "search"

PERMISSION_CODE = {
    "documentList": PERMISSION_DOCUMENT_LIST,
    "documentCreate": PERMISSION_DOCUMENT_CREATE,
    "documentEdit": PERMISSION_DOCUMENT_EDIT,
    "documentDelete": PERMISSION_DOCUMENT_DELETE,
    "documentReview": PERMISSION_DOCUMENT_REVIEW,
    "search": PERMISSION_SEARCH,
}

# 管理员隐含持有的操作权限（permissions.ts 中逐字保留，共 19 项）
ADMIN_OPERATION_PERMISSIONS = [
    "document:list",
    "document:create",
    "document:edit",
    "document:delete",
    "document:review",
    "document:category",
    "document:category:query",
    "document:tag",
    "document:version",
    "search",
    "system:user",
    "system:role",
    "system:permission",
    "system:permission:create",
    "system:permission:edit",
    "system:permission:delete",
    "system:team",
    "system:statistics",
    "system:settings",
]

PERMISSION_TYPE_MENU = 1
PERMISSION_TYPE_BUTTON = 2
PERMISSION_TYPE_API = 3

PERMISSION_SYSTEM_USER = "system:user"
PERMISSION_SYSTEM_ROLE = "system:role"
PERMISSION_SYSTEM_PERMISSION = "system:permission"
PERMISSION_SYSTEM_PERMISSION_CREATE = "system:permission:create"
PERMISSION_SYSTEM_PERMISSION_EDIT = "system:permission:edit"
PERMISSION_SYSTEM_PERMISSION_DELETE = "system:permission:delete"
PERMISSION_SYSTEM_TEAM = "system:team"
