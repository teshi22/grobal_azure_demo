"""Cosmos DB クライアント + ストアサービス

- ConversationStore: 会話メタデータ CRUD
- EventStore: SSE イベントログ (追記 + Change Feed 読み取り)
- EventNotifier: インメモリ SSE 即時通知
- CosmosCheckpointRepository: Agent Framework CheckpointRepository 実装
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.identity.aio import DefaultAzureCredential

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EventBus — SSE 接続にイベントを即時配信するインメモリ pub/sub
# ---------------------------------------------------------------------------
class EventBus:
    """per-conversation asyncio.Queue ベースのイベントバス。

    - subscribe() で Queue を取得、SSE ループが読む
    - publish() で全 subscriber に即座に配信
    - Cosmos 読み取りを SSE クリティカルパスから排除
    """

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, conversation_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(conversation_id, []).append(q)
        return q

    def unsubscribe(self, conversation_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(conversation_id)
        if subs:
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                del self._subscribers[conversation_id]

    def publish(self, conversation_id: str, event: dict) -> None:
        for q in self._subscribers.get(conversation_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


_event_bus = EventBus()


def get_event_bus() -> EventBus:
    return _event_bus

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
        await self._container.create_item(doc)
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
            await self._container.upsert_item(conv)

    async def update_status_direct(self, conv: dict, status: str) -> None:
        """既に取得済みの conv doc のステータスを更新 (再 get 不要)"""
        conv["status"] = status
        conv["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._container.upsert_item(conv)


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
        self._counters: dict[str, int] = {}

    async def append(
        self,
        conversation_id: str,
        event_type: str,
        data: dict[str, Any] | str,
        message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        """イベントを追記し、event_index を返す"""
        # インメモリカウンターで MAX クエリを省略
        if conversation_id in self._counters:
            event_index = self._counters[conversation_id] + 1
        else:
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

        self._counters[conversation_id] = event_index

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
        await self._container.create_item(doc)

        # SSE subscriber に即時配信
        _event_bus.publish(conversation_id, {
            "event_index": event_index,
            "event_type": event_type,
            "data": data_str,
        })

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
# TravelRequestStore — 出張申請データの読み取り
# ---------------------------------------------------------------------------
class TravelRequestStore:
    """MCP サーバーが書き込んだ出張申請データを読み取る"""

    def __init__(self):
        self._container = _get_container(settings.cosmos_travel_request_container)

    async def list_all(self, limit: int = 50) -> list[dict]:
        """全申請を新しい順に取得"""
        query = (
            "SELECT * FROM c ORDER BY c.submitted_at DESC OFFSET 0 LIMIT @limit"
        )
        params: list[dict] = [{"name": "@limit", "value": limit}]
        items: list[dict] = []
        async for item in self._container.query_items(
            query, parameters=params,
        ):
            items.append(item)
        return items

    async def get(self, request_id: str) -> dict | None:
        """request_id で申請を取得 (point read)"""
        try:
            return await self._container.read_item(
                request_id, partition_key=request_id,
            )
        except Exception:
            return None


_travel_request_store: TravelRequestStore | None = None


def get_travel_request_store() -> TravelRequestStore:
    global _travel_request_store
    if _travel_request_store is None:
        _travel_request_store = TravelRequestStore()
    return _travel_request_store


# ---------------------------------------------------------------------------
# CosmosCheckpointRepository (Agent Framework インターフェース実装)
# ---------------------------------------------------------------------------
class CosmosCheckpointRepository:
    """Agent Framework の CheckpointStorage プロトコルを Cosmos DB で実装する。

    WorkflowCheckpoint を JSON シリアライズして Cosmos DB に保存。
    checkpoint_id を conversation_id として partition key に使用。
    """

    def __init__(self):
        self._container = _get_container(settings.cosmos_checkpoint_container)

    async def save(self, checkpoint) -> str:
        """WorkflowCheckpoint を保存し checkpoint_id を返す"""
        import dataclasses

        doc = dataclasses.asdict(checkpoint)
        doc["id"] = checkpoint.checkpoint_id
        doc["conversation_id"] = checkpoint.checkpoint_id
        # Complex objects need safe JSON serialization
        doc["pending_request_info_events"] = json.loads(
            json.dumps(doc["pending_request_info_events"], default=str)
        )
        doc["messages"] = json.loads(
            json.dumps(doc["messages"], default=str)
        )
        doc["state"] = json.loads(
            json.dumps(doc["state"], default=str)
        )
        logger.info(
            f"Saving checkpoint: id={checkpoint.checkpoint_id}, "
            f"workflow={checkpoint.workflow_name}"
        )
        await self._container.upsert_item(doc)
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str):
        """checkpoint_id でチェックポイントを取得"""
        logger.info(f"Loading checkpoint: id={checkpoint_id}")
        try:
            doc = await self._container.read_item(
                checkpoint_id, partition_key=checkpoint_id
            )
            return self._doc_to_checkpoint(doc)
        except Exception:
            from agent_framework._workflows._checkpoint import (
                WorkflowCheckpointException,
            )
            raise WorkflowCheckpointException(
                f"Checkpoint {checkpoint_id} not found"
            )

    async def list_checkpoints(self, *, workflow_name: str) -> list:
        """workflow_name に一致するチェックポイントを一覧取得"""
        query = (
            "SELECT * FROM c WHERE c.workflow_name = @wn "
            "ORDER BY c.timestamp DESC"
        )
        params = [{"name": "@wn", "value": workflow_name}]
        items = [
            item
            async for item in self._container.query_items(
                query, parameters=params,
            )
        ]
        return [self._doc_to_checkpoint(i) for i in items]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        """workflow_name に一致するチェックポイント ID を一覧取得"""
        query = (
            "SELECT c.id FROM c WHERE c.workflow_name = @wn "
            "ORDER BY c.timestamp DESC"
        )
        params = [{"name": "@wn", "value": workflow_name}]
        return [
            item["id"]
            async for item in self._container.query_items(
                query, parameters=params,
            )
        ]

    async def delete(self, checkpoint_id: str) -> bool:
        """チェックポイントを削除"""
        try:
            await self._container.delete_item(
                checkpoint_id, partition_key=checkpoint_id
            )
            return True
        except Exception:
            logger.warning(f"Failed to delete checkpoint {checkpoint_id}")
            return False

    async def get_latest(self, *, workflow_name: str):
        """workflow_name の最新チェックポイントを取得"""
        checkpoints = await self.list_checkpoints(workflow_name=workflow_name)
        return checkpoints[0] if checkpoints else None

    def _doc_to_checkpoint(self, doc: dict):
        """Cosmos DB ドキュメントを WorkflowCheckpoint に変換"""
        from agent_framework._workflows._checkpoint import WorkflowCheckpoint

        return WorkflowCheckpoint(
            workflow_name=doc.get("workflow_name", ""),
            graph_signature_hash=doc.get("graph_signature_hash", ""),
            checkpoint_id=doc.get("checkpoint_id", doc["id"]),
            previous_checkpoint_id=doc.get("previous_checkpoint_id"),
            timestamp=doc.get("timestamp", ""),
            messages=doc.get("messages", {}),
            state=doc.get("state", {}),
            pending_request_info_events=doc.get("pending_request_info_events", {}),
            iteration_count=doc.get("iteration_count", 0),
            metadata=doc.get("metadata", {}),
            version=doc.get("version", "1"),
        )
