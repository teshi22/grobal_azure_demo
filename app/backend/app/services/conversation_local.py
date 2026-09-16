"""In-memory conversation stores for the explicit local stub mode."""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalConversationStore:
    def __init__(self):
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        conversation_id: str,
        user_id: str,
        *,
        scenario: str = "agent_framework_workflow",
    ) -> dict:
        now = _now()
        item = {
            "id": conversation_id,
            "user_id": user_id,
            "scenario": scenario,
            "status": "created",
            "foundry_response_id": None,
            "pending_request": None,
            "created_at": now,
            "updated_at": now,
        }
        async with self._lock:
            self._items[conversation_id] = item
        return copy.deepcopy(item)

    async def get(self, conversation_id: str) -> dict | None:
        item = self._items.get(conversation_id)
        return copy.deepcopy(item) if item is not None else None

    async def get_owned(
        self,
        conversation_id: str,
        user_id: str,
    ) -> dict | None:
        item = await self.get(conversation_id)
        if not item or item.get("user_id") != user_id:
            return None
        return item

    async def update(self, conversation_id: str, **changes: Any) -> dict:
        async with self._lock:
            item = self._items.get(conversation_id)
            if item is None:
                raise LookupError(f"Conversation {conversation_id} not found")
            item.update(copy.deepcopy(changes))
            item["updated_at"] = _now()
            return copy.deepcopy(item)

    async def update_status(self, conversation_id: str, status: str) -> None:
        await self.update(conversation_id, status=status)

    async def claim_message(
        self,
        conversation_id: str,
        idempotency_key: str,
        *,
        message_id: str,
        user_id: str,
        content: str,
    ) -> bool:
        async with self._lock:
            item = self._items.get(conversation_id)
            if item is None:
                return False
            if item.get("last_idempotency_key") == idempotency_key:
                return False
            if item.get("status") == "processing" and item.get("active_message"):
                return False
            item["last_idempotency_key"] = idempotency_key
            item["status"] = "processing"
            item["active_message"] = {
                "message_id": message_id,
                "user_id": user_id,
                "content": content,
                "idempotency_key": idempotency_key,
            }
            item["processing_lease_until"] = None
            item["updated_at"] = _now()
            return True

    async def claim_pending_message(
        self,
        conversation_id: str,
        *,
        lease_minutes: int = 10,
    ) -> dict[str, str] | None:
        async with self._lock:
            item = self._items.get(conversation_id)
            if item is None or item.get("status") != "processing":
                return None
            active_message = item.get("active_message")
            if not isinstance(active_message, dict):
                return None

            now = datetime.now(timezone.utc)
            lease_until = item.get("processing_lease_until")
            if lease_until:
                try:
                    if datetime.fromisoformat(str(lease_until)) > now:
                        return None
                except ValueError:
                    pass

            item["processing_lease_until"] = (
                now + timedelta(minutes=lease_minutes)
            ).isoformat()
            item["updated_at"] = now.isoformat()
            return {
                "message_id": str(active_message["message_id"]),
                "user_id": str(active_message["user_id"]),
                "content": str(active_message["content"]),
                "idempotency_key": str(active_message["idempotency_key"]),
            }

    async def list_pending_conversation_ids(self) -> list[str]:
        return [
            conversation_id
            for conversation_id, item in self._items.items()
            if item.get("status") == "processing"
            and isinstance(item.get("active_message"), dict)
        ]


class LocalEventStore:
    def __init__(self):
        self._items: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._last_event_ns = 0

    async def append(
        self,
        conversation_id: str,
        event_type: str,
        data: dict[str, Any] | str,
        message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        serialized = (
            json.dumps(data, ensure_ascii=False)
            if isinstance(data, dict)
            else data
        )
        async with self._lock:
            event_ns = max(time.time_ns(), self._last_event_ns + 1)
            self._last_event_ns = event_ns
            event_cursor = f"{event_ns:020d}-{uuid.uuid4().hex}"
            item = {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "event_cursor": event_cursor,
                "event_type": event_type,
                "data": serialized,
                "message_id": message_id,
                "idempotency_key": idempotency_key,
                "timestamp": _now(),
            }
            self._items.setdefault(conversation_id, []).append(item)
        return event_cursor

    async def get_events_after(
        self,
        conversation_id: str,
        after_cursor: str = "",
    ) -> list[dict]:
        items = self._items.get(conversation_id, [])
        return copy.deepcopy(
            [
                item
                for item in items
                if item["event_cursor"] > after_cursor
            ]
        )

    async def get_by_idempotency_key(
        self,
        conversation_id: str,
        idempotency_key: str,
    ) -> dict | None:
        for item in self._items.get(conversation_id, []):
            if item.get("idempotency_key") == idempotency_key:
                return copy.deepcopy(item)
        return None
