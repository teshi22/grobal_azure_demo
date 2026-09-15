"""Cosmos DB stores used by the authenticated BFF."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from azure.core import MatchConditions
from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)
from azure.identity.aio import DefaultAzureCredential

from app.config import settings

logger = logging.getLogger(__name__)

_cosmos_client: CosmosClient | None = None
_credential: DefaultAzureCredential | None = None
_conversation_store: ConversationStore | None = None
_event_store: EventStore | None = None
_travel_request_store: TravelRequestStore | None = None
_approval_grant_store: ApprovalGrantStore | None = None


def get_cosmos_client() -> CosmosClient:
    global _cosmos_client, _credential
    if not settings.cosmos_endpoint:
        raise RuntimeError("COSMOS_ENDPOINT is required")
    if _cosmos_client is None:
        _credential = DefaultAzureCredential()
        _cosmos_client = CosmosClient(
            settings.cosmos_endpoint,
            credential=_credential,
        )
    return _cosmos_client


async def close_cosmos_client() -> None:
    global _cosmos_client, _credential
    if _cosmos_client is not None:
        await _cosmos_client.close()
        _cosmos_client = None
    if _credential is not None:
        await _credential.close()
        _credential = None


def _get_container(container_name: str) -> ContainerProxy:
    database = get_cosmos_client().get_database_client(settings.cosmos_database)
    return database.get_container_client(container_name)


class ConversationStore:
    """Conversation ownership and Hosted Agent continuation state."""

    def __init__(self):
        self._container = _get_container(settings.cosmos_conversation_container)

    async def create(self, conversation_id: str, user_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": conversation_id,
            "user_id": user_id,
            "status": "created",
            "foundry_response_id": None,
            "pending_request": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._container.create_item(item)
        return item

    async def get(self, conversation_id: str) -> dict | None:
        try:
            return await self._container.read_item(
                item=conversation_id,
                partition_key=conversation_id,
            )
        except CosmosResourceNotFoundError:
            return None

    async def get_owned(self, conversation_id: str, user_id: str) -> dict | None:
        item = await self.get(conversation_id)
        if not item or item.get("user_id") != user_id:
            return None
        return item

    async def update(self, conversation_id: str, **changes: Any) -> dict:
        item = await self.get(conversation_id)
        if not item:
            raise LookupError(f"Conversation {conversation_id} not found")
        item.update(changes)
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        return await self._container.replace_item(
            item=conversation_id,
            body=item,
            etag=item.get("_etag"),
            match_condition=MatchConditions.IfNotModified,
        )

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
        item = await self.get(conversation_id)
        if not item:
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
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            await self._container.replace_item(
                item=conversation_id,
                body=item,
                etag=item.get("_etag"),
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as exc:
            if exc.status_code == 412:
                return False
            raise
        return True

    async def claim_pending_message(
        self,
        conversation_id: str,
        *,
        lease_minutes: int = 10,
    ) -> dict[str, str] | None:
        item = await self.get(conversation_id)
        if not item or item.get("status") != "processing":
            return None
        active_message = item.get("active_message")
        if not isinstance(active_message, dict):
            return None

        now = datetime.now(timezone.utc)
        lease_until = item.get("processing_lease_until")
        if lease_until:
            try:
                if datetime.fromisoformat(lease_until) > now:
                    return None
            except ValueError:
                logger.warning(
                    "Ignoring invalid processing lease for %s",
                    conversation_id,
                )

        item["processing_lease_until"] = (
            now + timedelta(minutes=lease_minutes)
        ).isoformat()
        item["updated_at"] = now.isoformat()
        try:
            await self._container.replace_item(
                item=conversation_id,
                body=item,
                etag=item.get("_etag"),
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as exc:
            if exc.status_code == 412:
                return None
            raise
        return {
            "message_id": str(active_message["message_id"]),
            "user_id": str(active_message["user_id"]),
            "content": str(active_message["content"]),
            "idempotency_key": str(active_message["idempotency_key"]),
        }

    async def list_pending_conversation_ids(self) -> list[str]:
        query = (
            "SELECT VALUE c.id FROM c "
            "WHERE c.status = 'processing' "
            "AND IS_DEFINED(c.active_message)"
        )
        return [str(item) async for item in self._container.query_items(query=query)]


def get_conversation_store() -> ConversationStore:
    global _conversation_store
    if _conversation_store is None:
        _conversation_store = ConversationStore()
    return _conversation_store


class EventStore:
    """Durable, replayable SSE event log partitioned by conversation."""

    def __init__(self):
        self._container = _get_container(settings.cosmos_event_container)

    async def append(
        self,
        conversation_id: str,
        event_type: str,
        data: dict[str, Any] | str,
        message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        event_cursor = f"{time.time_ns():020d}-{uuid.uuid4().hex}"
        serialized = (
            json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
        )
        item = {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "event_cursor": event_cursor,
            "event_type": event_type,
            "data": serialized,
            "message_id": message_id,
            "idempotency_key": idempotency_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._container.create_item(item)
        return event_cursor

    async def get_events_after(
        self,
        conversation_id: str,
        after_cursor: str = "",
    ) -> list[dict]:
        query = (
            "SELECT * FROM c WHERE c.conversation_id = @conversation_id "
            "AND c.event_cursor > @event_cursor ORDER BY c.event_cursor"
        )
        parameters = [
            {"name": "@conversation_id", "value": conversation_id},
            {"name": "@event_cursor", "value": after_cursor},
        ]
        return [
            item
            async for item in self._container.query_items(
                query=query,
                parameters=parameters,
                partition_key=conversation_id,
            )
        ]

    async def get_by_idempotency_key(
        self,
        conversation_id: str,
        idempotency_key: str,
    ) -> dict | None:
        query = (
            "SELECT TOP 1 * FROM c WHERE c.conversation_id = @conversation_id "
            "AND c.idempotency_key = @idempotency_key"
        )
        parameters = [
            {"name": "@conversation_id", "value": conversation_id},
            {"name": "@idempotency_key", "value": idempotency_key},
        ]
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            partition_key=conversation_id,
        ):
            return item
        return None


def get_event_store() -> EventStore:
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store


class TravelRequestStore:
    """Read travel requests written by the MCP submission service."""

    def __init__(self):
        self._container = _get_container(settings.cosmos_travel_request_container)

    async def list_by_user(self, user_id: str, limit: int = 50) -> list[dict]:
        query = (
            "SELECT TOP @limit * FROM c WHERE c.user_id = @user_id "
            "ORDER BY c.submitted_at DESC"
        )
        parameters = [
            {"name": "@limit", "value": limit},
            {"name": "@user_id", "value": user_id},
        ]
        return [
            item
            async for item in self._container.query_items(
                query=query,
                parameters=parameters,
            )
        ]

    async def get_owned(self, request_id: str, user_id: str) -> dict | None:
        try:
            item = await self._container.read_item(
                item=request_id,
                partition_key=request_id,
            )
        except CosmosResourceNotFoundError:
            return None
        return item if item.get("user_id") == user_id else None


def get_travel_request_store() -> TravelRequestStore:
    global _travel_request_store
    if _travel_request_store is None:
        _travel_request_store = TravelRequestStore()
    return _travel_request_store


class ApprovalGrantStore:
    """Short-lived grants issued after an authenticated HITL approval."""

    def __init__(self):
        self._container = _get_container(settings.cosmos_approval_grant_container)

    async def issue(
        self,
        *,
        user_id: str,
        conversation_id: str,
        call_id: str,
        plan_hash: str,
        ttl_minutes: int = 10,
    ) -> dict:
        grant_id = hashlib.sha256(
            f"{conversation_id}:{call_id}".encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc)
        item = {
            "id": grant_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "call_id": call_id,
            "plan_hash": plan_hash,
            "idempotency_key": hashlib.sha256(
                f"travel-request:{grant_id}".encode("utf-8")
            ).hexdigest(),
            "status": "issued",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
            "ttl": ttl_minutes * 60,
        }
        try:
            await self._container.create_item(item, if_none_match="*")
            return item
        except CosmosHttpResponseError as exc:
            if exc.status_code != 409:
                raise
            return await self._container.read_item(
                item=grant_id,
                partition_key=grant_id,
            )


def get_approval_grant_store() -> ApprovalGrantStore:
    global _approval_grant_store
    if _approval_grant_store is None:
        _approval_grant_store = ApprovalGrantStore()
    return _approval_grant_store
