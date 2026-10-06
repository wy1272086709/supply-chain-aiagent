"""团队服务 — 对应 src/team/team.service.ts。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.snowflake import next_snowflake_id
from app.db.models import Team, TeamMember, User
from app.modules.users import serializers
from app.common.now import utcnow


async def list_accessible_team_ids(db: AsyncSession, user_id: str) -> list[str]:
    """成员表身份 ∪ 担任负责人；已软删团队不计入。"""
    member_rows = await db.execute(
        select(TeamMember.team_id).where(TeamMember.user_id == user_id)
    )
    led = await db.execute(
        select(Team.id).where(Team.leader_id == user_id, Team.deleted.is_(False))
    )
    ids = list({str(r[0]) for r in member_rows.all()} | {str(r[0]) for r in led.all()})
    if not ids:
        return []
    teams = await db.execute(
        select(Team.id).where(Team.id.in_(ids), Team.deleted.is_(False))
    )
    return [str(r[0]) for r in teams.all()]


async def list_mine(db: AsyncSession, user_id: str) -> list[dict]:
    ids = await list_accessible_team_ids(db, user_id)
    if not ids:
        return []
    teams = (
        (
            await db.execute(
                select(Team)
                .where(Team.id.in_(ids), Team.deleted.is_(False))
                .order_by(Team.sort.asc(), Team.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [serializers.team_vo(t) for t in teams]


async def create(db: AsyncSession, dto: dict) -> dict:
    now = utcnow()
    team = Team(
        id=next_snowflake_id(),
        team_name=dto["teamName"],
        team_code=dto.get("teamCode"),
        description=dto.get("description"),
        leader_id=dto.get("leaderId"),
        parent_id=dto.get("parentId") or "0",
        sort=dto.get("sort") or 0,
        status=dto.get("status") if dto.get("status") is not None else 1,
        created_at=now,
        updated_at=now,
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return serializers.team_vo(team)


async def update(db: AsyncSession, team_id: str, dto: dict) -> dict:
    team = await _find_by_id_or_throw(db, team_id)
    field_map = {
        "teamName": "team_name",
        "teamCode": "team_code",
        "description": "description",
        "leaderId": "leader_id",
        "sort": "sort",
        "status": "status",
    }
    for field, column in field_map.items():
        if dto.get(field) is not None:
            setattr(team, column, dto[field])
    if dto.get("parentId") is not None:
        if dto["parentId"] == team_id:
            raise BadRequestError("父团队不能是自己")
        team.parent_id = dto["parentId"]
    team.updated_at = utcnow()
    await db.commit()
    return serializers.team_vo(team)


async def delete(db: AsyncSession, team_id: str) -> None:
    team = await _find_by_id_or_throw(db, team_id)
    child_count = (
        await db.execute(
            select(func.count()).select_from(Team).where(
                Team.parent_id == team_id, Team.deleted.is_(False)
            )
        )
    ).scalar_one()
    if child_count > 0:
        raise BadRequestError("存在子团队，无法删除")
    team.deleted = True
    team.updated_at = utcnow()
    await db.commit()
    await db.execute(sa_delete(TeamMember).where(TeamMember.team_id == team_id))
    await db.commit()


async def get_detail(db: AsyncSession, team_id: str) -> dict:
    team = await _find_by_id_or_throw(db, team_id)
    member_count = (
        await db.execute(
            select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)
        )
    ).scalar_one()
    return serializers.team_vo(team, member_count)


async def page(db: AsyncSession, query: dict) -> dict:
    page = query.get("page") or 1
    page_size = query.get("pageSize") or 20
    base = select(Team).where(Team.deleted.is_(False))
    count_q = select(func.count()).select_from(Team).where(Team.deleted.is_(False))
    keyword = (query.get("keyword") or "").strip()
    if keyword:
        kw = f"%{keyword}%"
        cond = Team.team_name.ilike(kw) | Team.team_code.ilike(kw)
        base = base.where(cond)
        count_q = count_q.where(cond)
    if query.get("status") is not None:
        cond = Team.status == query["status"]
        base = base.where(cond)
        count_q = count_q.where(cond)
    total = (await db.execute(count_q)).scalar_one()
    teams = (
        (
            await db.execute(
                base.order_by(Team.sort.asc(), Team.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [serializers.team_vo(t) for t in teams],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_tree(db: AsyncSession, root_only: bool = False) -> list[dict]:
    teams = (
        (
            await db.execute(
                select(Team)
                .where(Team.deleted.is_(False), Team.status == 1)
                .order_by(Team.sort.asc(), Team.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    vos = {t.id: serializers.team_vo(t) for t in teams}

    def build(parent_id: str) -> list[dict]:
        return [
            {**vos[t.id], "children": [] if root_only else build(t.id)}
            for t in teams
            if t.parent_id == parent_id
        ]

    root_nodes = [t for t in teams if not t.parent_id or t.parent_id == "0"]
    if root_only:
        return [vos[t.id] for t in root_nodes]
    return [{**vos[t.id], "children": build(t.id)} for t in root_nodes]


async def add_members(db: AsyncSession, team_id: str, user_ids: list[str]) -> bool:
    await _find_by_id_or_throw(db, team_id)
    users = (
        (
            await db.execute(select(User).where(User.id.in_(user_ids), User.deleted.is_(False)))
        )
        .scalars()
        .all()
    )
    if len(users) != len(set(user_ids)):
        raise NotFoundError("部分用户不存在")
    for user_id in dict.fromkeys(user_ids):
        exists = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(
            TeamMember(
                id=next_snowflake_id(),
                team_id=team_id,
                user_id=user_id,
                member_role="member",
                created_at=utcnow(),
            )
        )
    await db.commit()
    return True


async def remove_members(db: AsyncSession, team_id: str, user_ids: list[str]) -> bool:
    await _find_by_id_or_throw(db, team_id)
    await db.execute(
        sa_delete(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id.in_(user_ids)
        )
    )
    await db.commit()
    return True


async def list_members(db: AsyncSession, team_id: str) -> list[dict]:
    await _find_by_id_or_throw(db, team_id)
    rows = await db.execute(
        select(
            TeamMember.user_id.label("userId"),
            TeamMember.member_role.label("memberRole"),
            User.username.label("username"),
            User.real_name.label("realName"),
        )
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team_id, User.deleted.is_(False))
    )
    return [serializers.member_row(r) for r in rows.all()]


async def _find_by_id_or_throw(db: AsyncSession, team_id: str) -> Team:
    team = (
        await db.execute(select(Team).where(Team.id == team_id, Team.deleted.is_(False)))
    ).scalar_one_or_none()
    if not team:
        raise NotFoundError("团队不存在")
    return team
