"""Cosmos DB クライアント + ストアサービス

- ConversationStore: 会話メタデータ CRUD
- EventStore: SSE イベントログ (追記 + Change Feed 読み取り)
- CosmosCheckpointRepository: Agent Framework CheckpointRepository 実装
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.identity.aio import DefaultAzureCredential

from app.config import settings

logger = logging.getLogger(__name__)

_cosmos_client: CosmosClient | None = None
_conversation_store: ConversationStore | None = None
_event_store: EventStore | None = None


def get_cosmos_client() -> CosmosClient | None:
    global _cosmos_client
    if not settings.cosmos_endpoint:
        logger.warning("COSMOS_ENDPOINT not set — Cosmos DB disabled")
        return None
    if _cosmos_client is None:
        credential = DefaultAzureCredential()
        _cosmos_client = CosmosClient(settings.cosmos_endpoint, credential=credential)
    return _cosmos_client


def _get_container(container_name: str) -> ContainerProxy:
    client = get_cosmos_client()
    db = client.get_database_client(settings.cosmos_database)
    return db.get_container_client(container_name)


# ---------------------------------------------------------------------------
# ConversationStore
# ---------------------------------------------------------------------------
class ConversationStore:
    """会話メタデータの CRUD"""

    def __init__(self):
        self._container = _get_container(settings.cosmos_conversation_container)

    async def create(self, conversation_id: str, user_id: str) -> dict:
        doc = {
            "id": conversation_id,
            "user_id": user_id,
            "status": "created",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._container.create_item(doc, partition_key=user_id)
        return doc

    async def get(self, conversation_id: str) -> dict | None:
        query = "SELECT * FROM c WHERE c.id = @id"
        params = [{"name": "@id", "value": conversation_id}]
        items = [
            item
            async for item in self._container.query_items(
                query, parameters=params, max_item_count=1
            )
        ]
        return items[0] if items else None

    async def update_status(self, conversation_id: str, status: str) -> None:
        conv = await self.get(conversation_id)
        if conv:
            conv["status"] = status
            conv["updated_at"] = datetime.now(timezone.utc).isoformat()
            await self._container.upsert_item(conv, partition_key=conv["user_id"])


def get_conversation_store() -> ConversationStore:
    global _conversation_store
    if _conversation_store is None:
        _conversation_store = ConversationStore()
    return _conversation_store


# ---------------------------------------------------------------------------
# EventStore
# ---------------------------------------------------------------------------
class EventStore:
    """SSE イベントログの追記・読み取り

    各イベントは conversation_id でパーティション、event_index で順序付け。
    """

    def __init__(self):
        self._container = _get_container(settings.cosmos_event_container)

    async def append(
        self,
        conversation_id: str,
        event_type: str,
        data: dict[str, Any] | str,
        message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        """イベントを追記し、event_index を返す"""
        # 現在の最大 event_index を取得
        query = (
            "SELECT VALUE MAX(c.event_index) FROM c "
            "WHERE c.conversation_id = @cid"
        )
        params = [{"name": "@cid", "value": conversation_id}]
        results = [
            item
            async for item in self._container.query_items(query, parameters=params)
        ]
        max_index = results[0] if results and results[0] is not None else -1
        event_index = max_index + 1

        data_str = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data

        doc = {
            "id": f"{conversation_id}_{event_index}",
            "conversation_id": conversation_id,
            "event_index": event_index,
            "event_type": event_type,
            "data": data_str,
            "message_id": message_id,
            "idempotency_key": idempotency_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._container.create_item(doc, partition_key=conversation_id)
        return event_index

    async def get_events_after(
        self, conversation_id: str, after_index: int = -1
    ) -> list[dict]:
        """指定インデックス以降のイベントを取得"""
        query = (
            "SELECT * FROM c "
            "WHERE c.conversation_id = @cid AND c.event_index > @idx "
            "ORDER BY c.event_index"
        )
        params = [
            {"name": "@cid", "value": conversation_id},
            {"name": "@idx", "value": after_index},
        ]
        return [
            item
            async for item in self._container.query_items(query, parameters=params)
        ]

    async def get_by_idempotency_key(
        self, conversation_id: str, idempotency_key: str
    ) -> dict | None:
        """冪等性キーで既存イベントを検索"""
        query = (
            "SELECT * FROM c "
            "WHERE c.conversation_id = @cid AND c.idempotency_key = @key"
        )
        params = [
            {"name": "@cid", "value": conversation_id},
            {"name": "@key", "value": idempotency_key},
        ]
        items = [
            item
            async for item in self._container.query_items(
                query, parameters=params, max_item_count=1
            )
        ]
        return items[0] if items else None


def get_event_store() -> EventStore:
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store


# ---------------------------------------------------------------------------
# CosmosCheckpointRepository (Agent Framework インターフェース実装)
# ---------------------------------------------------------------------------
class CosmosCheckpointRepository:
    """Agent Framework の CheckpointRepository を Cosmos DB で実装する。

    checkpoint_id ごとに 1 ドキュメントとして保存。
    conversation_id をパーティションキーとして使用。
    """

    def __init__(self):
        self._container = _get_container(settings.cosmos_checkpoint_container)

    async def save(self, conversation_id: str, checkpoint_data: dict) -> str:
        """チェックポイントを保存"""
        import uuid

        checkpoint_id = checkpoint_data.get("checkpoint_id", str(uuid.uuid4()))
        doc = {
            "id": checkpoint_id,
            "conversation_id": conversation_id,
            **checkpoint_data,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._container.upsert_item(doc, partition_key=conversation_id)
        return checkpoint_id

    async def load(self, conversation_id: str) -> dict | None:
        """最新チェックポイントを取得"""
        query = (
            "SELECT * FROM c "
            "WHERE c.conversation_id = @cid "
            "ORDER BY c.saved_at DESC"
        )
        params = [{"name": "@cid", "value": conversation_id}]
        items = [
            item
            async for item in self._container.query_items(
                query, parameters=params, max_item_count=1
            )
        ]
        return items[0] if items else None

    async def delete(self, conversation_id: str, checkpoint_id: str) -> None:
        """チェックポイントを削除"""
        try:
            await self._container.delete_item(
                checkpoint_id, partition_key=conversation_id
            )
        except Exception:
            logger.warning(f"Failed to delete checkpoint {checkpoint_id}")
