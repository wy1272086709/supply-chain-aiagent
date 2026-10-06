"""实体 → 驼峰 JSON dict 序列化（与 TypeORM 实体属性名一致）。"""

from __future__ import annotations

from app.common.serialization import iso_z
from app.db.models import Document, Permission, Role, Team, TeamMember, User, DocumentReview


def user_vo(user: User, role_codes: list[str]) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "realName": user.real_name,
        "avatar": user.avatar,
        "status": user.status,
        "lastLoginAt": iso_z(user.last_login_at),
        "createdAt": iso_z(user.created_at),
        "updatedAt": iso_z(user.updated_at),
        "roleCodes": role_codes,
    }


def role_vo(role: Role) -> dict:
    return {
        "id": role.id,
        "roleName": role.role_name,
        "roleCode": role.role_code,
        "description": role.description,
        "status": role.status,
    }


def permission_vo(perm: Permission) -> dict:
    return {
        "id": perm.id,
        "parentId": perm.parent_id,
        "permissionName": perm.permission_name,
        "permissionCode": perm.permission_code,
        "permissionType": perm.permission_type,
        "menuUrl": perm.menu_url,
        "apiUrl": perm.api_url,
        "method": perm.method,
        "icon": perm.icon,
        "sort": perm.sort,
        "status": perm.status,
        "createdAt": iso_z(perm.created_at),
        "updatedAt": iso_z(perm.updated_at),
    }


def team_vo(team: Team, member_count: int | None = None) -> dict:
    data = {
        "id": team.id,
        "teamName": team.team_name,
        "teamCode": team.team_code,
        "description": team.description,
        "leaderId": team.leader_id,
        "parentId": team.parent_id,
        "sort": team.sort,
        "status": team.status,
        "createdAt": iso_z(team.created_at),
        "updatedAt": iso_z(team.updated_at),
    }
    if member_count is not None:
        data["memberCount"] = member_count
    return data


def document_vo(doc: Document) -> dict:
    return {
        "id": doc.id,
        "title": doc.title,
        "contentId": doc.content_id,
        "summary": doc.summary,
        "categoryId": doc.category_id,
        "teamId": doc.team_id,
        "authorId": doc.author_id,
        "coverImage": doc.cover_image,
        "tags": doc.tags,
        "status": doc.status,
        "remark": doc.remark,
        "viewCount": doc.view_count,
        "likeCount": doc.like_count,
        "commentCount": doc.comment_count,
        "favouriteCount": doc.favourite_count,
        "wordCount": doc.word_count,
        "publishTime": iso_z(doc.publish_time),
        "isPublic": doc.is_public,
        "createdAt": iso_z(doc.created_at),
        "updatedAt": iso_z(doc.updated_at),
        "createBy": doc.create_by,
        "updateBy": doc.update_by,
        "deleted": doc.deleted,
    }


def review_vo(review: DocumentReview) -> dict:
    return {
        "id": review.id,
        "documentId": review.document_id,
        "reviewerId": review.reviewer_id,
        "reviewerName": review.reviewer_name,
        "reviewResult": review.review_result,
        "reviewComment": review.review_comment,
        "beforeStatus": review.before_status,
        "reviewedAt": iso_z(review.reviewed_at),
        "createdAt": iso_z(review.created_at),
    }


def member_row(row) -> dict:
    return {
        "userId": str(row.userId),
        "memberRole": row.memberRole,
        "username": row.username,
        "realName": row.realName,
    }
