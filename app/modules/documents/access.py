"""文档访问控制 — 对应 src/document/document-access.ts（含 ES / Neo4j 可见性助手）。"""

from __future__ import annotations

from app.common.constants import ROLE_ADMIN, ROLE_REVIEWER
from app.modules.documents.status import DocumentStatus


def access_from_user(user: dict) -> dict:
    roles = user.get("roles") or []
    return {
        "unrestricted": ROLE_ADMIN in roles or ROLE_REVIEWER in roles,
        "userId": user["userId"],
        "teamIds": user.get("teamIds") or [],
    }


def can_read_document(doc, scope: dict) -> bool:
    if scope["unrestricted"]:
        return True
    if doc.author_id and doc.author_id == scope["userId"]:
        return True
    if doc.status != DocumentStatus.PUBLISHED:
        return False
    if doc.is_public:
        return True
    return bool(doc.team_id and doc.team_id in scope["teamIds"])


def can_write_document(doc, user: dict) -> bool:
    if ROLE_ADMIN in (user.get("roles") or []):
        return True
    return bool(doc.author_id and doc.author_id == user["userId"])


ES_CHUNK_VISIBILITY_FIELDS = {"isPublic": "is_public", "authorId": "author_id", "teamId": "team_id"}
ES_DOC_VISIBILITY_FIELDS = {"isPublic": "isPublic", "authorId": "authorId", "teamId": "teamId"}


def es_visibility_filter(scope: dict, fields: dict) -> dict | None:
    """ES 可见性 filter（只筛不打分）；管理员/审核员返回 None。"""
    if scope["unrestricted"]:
        return None
    should = [
        {"term": {fields["isPublic"]: True}},
        {"term": {fields["authorId"]: scope["userId"]}},
    ]
    if scope["teamIds"]:
        should.append({"terms": {fields["teamId"]: scope["teamIds"]}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


def wrap_es_query(query: dict, filter_: dict | None) -> dict:
    if not filter_:
        return query
    return {"bool": {"must": [query], "filter": [filter_]}}


def neo4j_access_params(scope: dict | None = None) -> dict:
    return {
        "unrestricted": (not scope) or scope["unrestricted"],
        "accessUserId": (scope or {}).get("userId", ""),
        "accessTeamIds": (scope or {}).get("teamIds", []),
    }


def neo4j_document_access_where(alias: str = "d") -> str:
    return (
        f"($unrestricted OR {alias}.authorId = $accessUserId "
        f"OR {alias}.isPublic = true OR {alias}.teamId IN $accessTeamIds)"
    )
