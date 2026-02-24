from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from .models import MessageSnapshot, StreamTarget, TopicDraft, TopicRecord

GetKV = Callable[[str, Any], Awaitable[Any]]
PutKV = Callable[[str, Any], Awaitable[None]]
DeleteKV = Callable[[str], Awaitable[None]]


class AstrBotKVStore:
    """AstrBot 插件 KV 存储适配层。"""

    _TOPIC_KEY = "topics"
    _STREAM_KEY = "streams"
    _MESSAGE_KEY = "messages"

    def __init__(self, get_kv: GetKV, put_kv: PutKV, delete_kv: DeleteKV):
        self._get_kv = get_kv
        self._put_kv = put_kv
        self._delete_kv = delete_kv

    async def create_topic(self, draft: TopicDraft, *, now: float | None = None) -> int:
        ts = now if now is not None else time.time()
        bucket = await self._get_topics_bucket()

        next_id = int(bucket.get("next_id", 1))
        bucket["next_id"] = next_id + 1
        bucket.setdefault("items", {})[str(next_id)] = {
            "id": next_id,
            "title": draft.title,
            "description": draft.description,
            "priority": max(int(draft.priority), 1),
            "enabled": bool(draft.enabled),
            "use_count": 0,
            "last_used_at": 0.0,
            "created_at": ts,
            "updated_at": ts,
        }

        await self._put_kv(self._TOPIC_KEY, bucket)
        return next_id

    async def delete_topic(self, topic_id: int) -> bool:
        bucket = await self._get_topics_bucket()
        removed = bucket.setdefault("items", {}).pop(str(topic_id), None)
        await self._put_kv(self._TOPIC_KEY, bucket)
        return removed is not None

    async def list_topics(self, *, enabled_only: bool = False) -> list[TopicRecord]:
        bucket = await self._get_topics_bucket()
        items = bucket.get("items", {})

        records: list[TopicRecord] = []
        for value in items.values():
            record = self._to_topic_record(value)
            if record is None:
                continue
            if enabled_only and not record.enabled:
                continue
            records.append(record)

        records.sort(key=lambda item: (-item.priority, item.id))
        return records

    async def mark_topic_used(self, topic_id: int, *, now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        bucket = await self._get_topics_bucket()
        items = bucket.setdefault("items", {})
        key = str(topic_id)
        if key not in items:
            return

        item = items[key]
        item["use_count"] = int(item.get("use_count", 0)) + 1
        item["last_used_at"] = ts
        item["updated_at"] = ts
        await self._put_kv(self._TOPIC_KEY, bucket)

    async def bind_stream(
        self,
        *,
        unified_msg_origin: str,
        session_name: str,
        platform: str,
        is_group: bool,
        now: float | None = None,
    ) -> None:
        ts = now if now is not None else time.time()
        bucket = await self._get_stream_bucket()
        items = bucket.setdefault("items", {})

        old = items.get(unified_msg_origin, {})
        items[unified_msg_origin] = {
            "unified_msg_origin": unified_msg_origin,
            "session_name": session_name,
            "platform": platform,
            "is_group": bool(is_group),
            "active": True,
            "last_user_message_ts": float(old.get("last_user_message_ts", ts)),
            "last_bot_initiate_ts": float(old.get("last_bot_initiate_ts", 0.0)),
            "created_at": float(old.get("created_at", ts)),
            "updated_at": ts,
        }

        await self._put_kv(self._STREAM_KEY, bucket)

    async def deactivate_stream(self, unified_msg_origin: str, *, now: float | None = None) -> bool:
        ts = now if now is not None else time.time()
        bucket = await self._get_stream_bucket()
        items = bucket.setdefault("items", {})
        target = items.get(unified_msg_origin)
        if not target:
            return False

        target["active"] = False
        target["updated_at"] = ts
        await self._put_kv(self._STREAM_KEY, bucket)
        return True

    async def get_stream(self, unified_msg_origin: str) -> StreamTarget | None:
        bucket = await self._get_stream_bucket()
        value = bucket.get("items", {}).get(unified_msg_origin)
        if not value:
            return None

        return self._to_stream_target(value)

    async def list_active_streams(self) -> list[StreamTarget]:
        bucket = await self._get_stream_bucket()
        items = bucket.get("items", {})

        streams: list[StreamTarget] = []
        for value in items.values():
            target = self._to_stream_target(value)
            if target and target.active:
                streams.append(target)

        streams.sort(key=lambda item: item.updated_at, reverse=True)
        return streams

    async def touch_user_message(self, unified_msg_origin: str, *, now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        bucket = await self._get_stream_bucket()
        items = bucket.setdefault("items", {})
        target = items.get(unified_msg_origin)
        if not target:
            return

        target["last_user_message_ts"] = ts
        target["updated_at"] = ts
        await self._put_kv(self._STREAM_KEY, bucket)

    async def mark_bot_initiated(self, unified_msg_origin: str, *, now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        bucket = await self._get_stream_bucket()
        items = bucket.setdefault("items", {})
        target = items.get(unified_msg_origin)
        if not target:
            return

        target["last_bot_initiate_ts"] = ts
        target["updated_at"] = ts
        await self._put_kv(self._STREAM_KEY, bucket)

    async def append_message(
        self,
        *,
        unified_msg_origin: str,
        sender_id: str,
        sender_name: str,
        content: str,
        created_at: float,
        max_records: int,
    ) -> None:
        bucket = await self._get_message_bucket()
        items = bucket.setdefault("items", {})
        queue = list(items.get(unified_msg_origin, []))

        queue.append(
            {
                "unified_msg_origin": unified_msg_origin,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content": content,
                "created_at": created_at,
            }
        )

        limit = max(int(max_records), 1)
        queue = sorted(queue, key=lambda item: float(item.get("created_at", 0.0)), reverse=True)[:limit]

        items[unified_msg_origin] = queue
        await self._put_kv(self._MESSAGE_KEY, bucket)

    async def list_recent_messages(self, unified_msg_origin: str, *, limit: int = 20) -> list[MessageSnapshot]:
        bucket = await self._get_message_bucket()
        queue = list(bucket.get("items", {}).get(unified_msg_origin, []))

        queue = sorted(queue, key=lambda item: float(item.get("created_at", 0.0)), reverse=True)[: max(int(limit), 1)]

        records: list[MessageSnapshot] = []
        for item in queue:
            records.append(
                MessageSnapshot(
                    unified_msg_origin=str(item.get("unified_msg_origin", unified_msg_origin)),
                    sender_id=str(item.get("sender_id", "unknown")),
                    sender_name=str(item.get("sender_name", "unknown")),
                    content=str(item.get("content", "")),
                    created_at=float(item.get("created_at", 0.0)),
                )
            )
        return records

    async def reset_all(self) -> None:
        await self._delete_kv(self._TOPIC_KEY)
        await self._delete_kv(self._STREAM_KEY)
        await self._delete_kv(self._MESSAGE_KEY)

    async def _get_topics_bucket(self) -> dict[str, Any]:
        value = await self._get_kv(self._TOPIC_KEY, {"next_id": 1, "items": {}})
        if not isinstance(value, dict):
            return {"next_id": 1, "items": {}}

        value.setdefault("next_id", 1)
        value.setdefault("items", {})
        if not isinstance(value["items"], dict):
            value["items"] = {}
        return value

    async def _get_stream_bucket(self) -> dict[str, Any]:
        value = await self._get_kv(self._STREAM_KEY, {"items": {}})
        if not isinstance(value, dict):
            return {"items": {}}

        value.setdefault("items", {})
        if not isinstance(value["items"], dict):
            value["items"] = {}
        return value

    async def _get_message_bucket(self) -> dict[str, Any]:
        value = await self._get_kv(self._MESSAGE_KEY, {"items": {}})
        if not isinstance(value, dict):
            return {"items": {}}

        value.setdefault("items", {})
        if not isinstance(value["items"], dict):
            value["items"] = {}
        return value

    @staticmethod
    def _to_topic_record(value: Any) -> TopicRecord | None:
        if not isinstance(value, dict):
            return None

        try:
            return TopicRecord(
                id=int(value.get("id", 0)),
                title=str(value.get("title", "")),
                description=str(value.get("description", "")),
                priority=max(int(value.get("priority", 1)), 1),
                enabled=bool(value.get("enabled", True)),
                use_count=max(int(value.get("use_count", 0)), 0),
                last_used_at=float(value.get("last_used_at", 0.0)),
                created_at=float(value.get("created_at", 0.0)),
                updated_at=float(value.get("updated_at", 0.0)),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_stream_target(value: Any) -> StreamTarget | None:
        if not isinstance(value, dict):
            return None

        try:
            return StreamTarget(
                unified_msg_origin=str(value.get("unified_msg_origin", "")),
                session_name=str(value.get("session_name", "")),
                platform=str(value.get("platform", "unknown")),
                is_group=bool(value.get("is_group", False)),
                active=bool(value.get("active", False)),
                last_user_message_ts=float(value.get("last_user_message_ts", 0.0)),
                last_bot_initiate_ts=float(value.get("last_bot_initiate_ts", 0.0)),
                created_at=float(value.get("created_at", 0.0)),
                updated_at=float(value.get("updated_at", 0.0)),
            )
        except (TypeError, ValueError):
            return None
