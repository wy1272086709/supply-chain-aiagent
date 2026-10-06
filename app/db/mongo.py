"""MongoDB（Motor）— document_content 集合，对应 Mongoose DocumentContent 模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_mongo_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(get_settings().mongo_uri)
        _db = _client.get_default_database()
    return _db


def content_collection():
    return get_mongo_db()["document_content"]


async def create_content(document_id: str, content: str, content_summary: str = "") -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "documentId": document_id,
        "content": content,
        "contentLength": len(content),
        "contentSummary": content_summary,
        "version": 1,
        "deleted": False,
        "createdAt": now,
        "updatedAt": now,
    }
    result = await content_collection().insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def find_content(content_id: str) -> dict | None:
    from bson import ObjectId

    if not content_id:
        return None
    try:
        oid = ObjectId(content_id)
    except Exception:
        return None
    return await content_collection().find_one({"_id": oid, "deleted": False})


async def update_content(content_id: str, content: str, content_summary: str) -> bool:
    from bson import ObjectId

    try:
        oid = ObjectId(content_id)
    except Exception:
        return False
    result = await content_collection().update_one(
        {"_id": oid},
        {
            "$set": {
                "content": content,
                "contentLength": len(content),
                "contentSummary": content_summary,
                "updatedAt": datetime.now(timezone.utc),
            },
            "$inc": {"version": 1},
        },
    )
    return result.modified_count > 0


async def soft_delete_content(content_id: str) -> None:
    from bson import ObjectId

    try:
        oid = ObjectId(content_id)
    except Exception:
        return
    await content_collection().update_one(
        {"_id": oid}, {"$set": {"deleted": True, "updatedAt": datetime.now(timezone.utc)}}
    )


async def hard_delete_content(content_id: str) -> None:
    from bson import ObjectId

    try:
        oid = ObjectId(content_id)
    except Exception:
        return
    await content_collection().delete_one({"_id": oid})


async def dispose_mongo() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None


def to_str(value: Any) -> str:
    return str(value)
