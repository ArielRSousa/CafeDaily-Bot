from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def connect_db() -> AsyncIOMotorDatabase:
    global _client, _db
    uri = os.environ["MONGODB_URI"]
    db_name = os.environ.get("MONGODB_DB_NAME", "cafedaily")

    last_error: Optional[BaseException] = None
    for attempt in range(1, 11):
        try:
            _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
            _db = _client[db_name]
            await _db.command("ping")
            await _ensure_indexes(_db)
            logger.info("MongoDB conectado (tentativa %s).", attempt)
            return _db
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "MongoDB indisponível (tentativa %s/10): %s",
                attempt,
                exc,
            )
            if _client is not None:
                _client.close()
                _client = None
                _db = None
            await asyncio.sleep(2)

    assert last_error is not None
    raise last_error


async def close_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB não inicializado; chame connect_db() antes.")
    return _db


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db["dailies"]
    await coll.create_index([("submitted_at", -1)])
    await coll.create_index([("guild_id", 1), ("user_id", 1), ("submitted_at", -1)])


async def insert_daily(
    *,
    guild_id: int,
    user_id: int,
    username: str,
    project: str,
    yesterday: str,
    today: str,
    tomorrow: str,
    blockers: str,
    submitted_at: datetime,
    admin_message_id: Optional[int] = None,
) -> ObjectId:
    doc: dict[str, Any] = {
        "guild_id": guild_id,
        "user_id": user_id,
        "username": username,
        "project": project,
        "yesterday": yesterday,
        "today": today,
        "tomorrow": tomorrow,
        "blockers": blockers,
        "submitted_at": submitted_at,
        "admin_message_id": admin_message_id,
    }
    result = await get_db()["dailies"].insert_one(doc)
    return result.inserted_id


async def set_admin_message_id(daily_id: ObjectId, admin_message_id: int) -> None:
    await get_db()["dailies"].update_one(
        {"_id": daily_id},
        {"$set": {"admin_message_id": admin_message_id}},
    )


async def fetch_submitted_at_for_user(guild_id: int, user_id: int) -> list[datetime]:
    coll = get_db()["dailies"]
    cursor = coll.find(
        {"guild_id": guild_id, "user_id": user_id},
        {"submitted_at": 1, "_id": 0},
    ).sort("submitted_at", 1)
    out: list[datetime] = []
    async for doc in cursor:
        ts = doc.get("submitted_at")
        if isinstance(ts, datetime):
            out.append(ts)
    return out


async def fetch_submitted_at_by_user_for_guild(guild_id: int) -> dict[int, list[datetime]]:
    """Todos os submitted_at agrupados por user_id (para ranking)."""
    coll = get_db()["dailies"]
    cursor = coll.find({"guild_id": guild_id}, {"user_id": 1, "submitted_at": 1, "_id": 0})
    by_user: dict[int, list[datetime]] = {}
    async for doc in cursor:
        uid = doc.get("user_id")
        ts = doc.get("submitted_at")
        if not isinstance(uid, int) or not isinstance(ts, datetime):
            continue
        by_user.setdefault(uid, []).append(ts)
    for uid in by_user:
        by_user[uid].sort()
    return by_user
