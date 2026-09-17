"""Cosmos DB stores used by the authenticated BFF."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from azure.core import MatchConditions
from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.cosmos.exceptions import (
    CosmosBatchOperationError,
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
_evaluation_case_store: EvaluationCaseStore | None = None
_evaluation_run_store: EvaluationRunStore | None = None
_evaluation_result_store: EvaluationResultStore | None = None


class StoreNotFoundError(LookupError):
    """The requested Cosmos document does not exist."""


class StoreConflictError(RuntimeError):
    """A create or optimistic-concurrency operation conflicted."""


class StorePersistenceError(RuntimeError):
    """A Cosmos write failed for a reason other than a conflict."""


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
    global _conversation_store, _event_store, _travel_request_store
    global _evaluation_case_store
    global _evaluation_run_store, _evaluation_result_store
    if _cosmos_client is not None:
        await _cosmos_client.close()
        _cosmos_client = None
    if _credential is not None:
        await _credential.close()
        _credential = None
    _conversation_store = None
    _event_store = None
    _travel_request_store = None
    _evaluation_case_store = None
    _evaluation_run_store = None
    _evaluation_result_store = None


def _get_container(container_name: str) -> ContainerProxy:
    database = get_cosmos_client().get_database_client(settings.cosmos_database)
    return database.get_container_client(container_name)


class ConversationStore:
    """Conversation ownership and Foundry Agent continuation state."""

    def __init__(self):
        self._container = _get_container(settings.cosmos_conversation_container)

    async def create(
        self,
        conversation_id: str,
        user_id: str,
        *,
        scenario: str = "agent_framework_workflow",
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
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
        await self._container.create_item(item)
        return item

    async def get(self, conversation_id: str) -> dict | None:
        try:
            item = await self._container.read_item(
                item=conversation_id,
                partition_key=conversation_id,
            )
        except CosmosResourceNotFoundError:
            return None
        item.setdefault("scenario", "agent_framework_workflow")
        return item

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
        if settings.evaluation_mode == "stub":
            from app.services.conversation_local import LocalConversationStore

            _conversation_store = LocalConversationStore()
        else:
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
        if settings.evaluation_mode == "stub":
            from app.services.conversation_local import LocalEventStore

            _event_store = LocalEventStore()
        else:
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_conflict(exc: CosmosHttpResponseError) -> bool:
    return exc.status_code in (409, 412)


class EvaluationCaseStore:
    """Versioned evaluation cases partitioned by dataset ID."""

    def __init__(self):
        self._container = _get_container(
            settings.cosmos_evaluation_case_container
        )

    async def list(
        self,
        dataset_id: str,
        *,
        enabled: bool | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM c WHERE c.dataset_id = @dataset_id"
        parameters: list[dict[str, Any]] = [
            {"name": "@dataset_id", "value": dataset_id}
        ]
        if enabled is not None:
            query += " AND c.enabled = @enabled"
            parameters.append({"name": "@enabled", "value": enabled})
        query += " ORDER BY c.id"
        return [
            item
            async for item in self._container.query_items(
                query=query,
                parameters=parameters,
                partition_key=dataset_id,
            )
        ]

    async def get(self, case_id: str, dataset_id: str) -> dict | None:
        try:
            return await self._container.read_item(
                item=case_id,
                partition_key=dataset_id,
            )
        except CosmosResourceNotFoundError:
            return None

    async def create(self, case: dict[str, Any], creator_id: str) -> dict:
        now = _now()
        item = {
            **case,
            "version": 1,
            "created_by": creator_id,
            "updated_by": creator_id,
            "created_at": now,
            "updated_at": now,
        }
        try:
            return await self._container.create_item(
                item,
                if_none_match="*",
            )
        except CosmosHttpResponseError as exc:
            if _is_conflict(exc):
                raise StoreConflictError(
                    f"Evaluation case {item['id']} already exists"
                ) from exc
            raise StorePersistenceError(
                f"Failed to create evaluation case {item['id']}: {exc}"
            ) from exc

    async def update(
        self,
        case_id: str,
        dataset_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int,
        updated_by: str,
    ) -> dict:
        item = await self.get(case_id, dataset_id)
        if item is None:
            raise StoreNotFoundError(
                f"Evaluation case {case_id} not found"
            )
        if int(item.get("version", 1)) != expected_version:
            raise StoreConflictError(
                f"Evaluation case {case_id} was modified"
            )
        item.update(changes)
        item["id"] = case_id
        item["dataset_id"] = dataset_id
        item["version"] = expected_version + 1
        item["updated_by"] = updated_by
        item["updated_at"] = _now()
        try:
            return await self._container.replace_item(
                item=case_id,
                body=item,
                etag=item.get("_etag"),
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as exc:
            if _is_conflict(exc):
                raise StoreConflictError(
                    f"Evaluation case {case_id} was modified"
                ) from exc
            raise StorePersistenceError(
                f"Failed to update evaluation case {case_id}: {exc}"
            ) from exc


def get_evaluation_case_store() -> EvaluationCaseStore:
    global _evaluation_case_store
    if _evaluation_case_store is None:
        if settings.evaluation_mode == "stub":
            from app.services.evaluation_local import LocalEvaluationCaseStore

            _evaluation_case_store = LocalEvaluationCaseStore()
        else:
            _evaluation_case_store = EvaluationCaseStore()
    return _evaluation_case_store


class EvaluationRunStore:
    """Comparison run metadata partitioned by its ID."""

    def __init__(self):
        self._container = _get_container(
            settings.cosmos_evaluation_run_container
        )

    async def list(self, limit: int = 50) -> list[dict]:
        query = "SELECT TOP @limit * FROM c ORDER BY c.created_at DESC"
        return [
            item
            async for item in self._container.query_items(
                query=query,
                parameters=[{"name": "@limit", "value": limit}],
            )
        ]

    async def get(self, comparison_id: str) -> dict | None:
        try:
            return await self._container.read_item(
                item=comparison_id,
                partition_key=comparison_id,
            )
        except CosmosResourceNotFoundError:
            return None

    async def create(self, run: dict[str, Any], creator_id: str) -> dict:
        now = _now()
        item = {
            **run,
            "version": 1,
            "created_by": creator_id,
            "created_at": now,
            "updated_at": now,
        }
        try:
            return await self._container.create_item(
                item,
                if_none_match="*",
            )
        except CosmosHttpResponseError as exc:
            if _is_conflict(exc):
                raise StoreConflictError(
                    f"Evaluation run {item['id']} already exists"
                ) from exc
            raise StorePersistenceError(
                f"Failed to create evaluation run {item['id']}: {exc}"
            ) from exc

    async def update(
        self,
        comparison_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict:
        item = await self.get(comparison_id)
        if item is None:
            raise StoreNotFoundError(
                f"Evaluation run {comparison_id} not found"
            )
        if int(item.get("version", 1)) != expected_version:
            raise StoreConflictError(
                f"Evaluation run {comparison_id} was modified"
            )
        item.update(changes)
        item["id"] = comparison_id
        item["version"] = expected_version + 1
        item["updated_at"] = _now()
        try:
            return await self._container.replace_item(
                item=comparison_id,
                body=item,
                etag=item.get("_etag"),
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as exc:
            if _is_conflict(exc):
                raise StoreConflictError(
                    f"Evaluation run {comparison_id} was modified"
                ) from exc
            raise StorePersistenceError(
                f"Failed to update evaluation run {comparison_id}: {exc}"
            ) from exc


def get_evaluation_run_store() -> EvaluationRunStore:
    global _evaluation_run_store
    if _evaluation_run_store is None:
        if settings.evaluation_mode == "stub":
            from app.services.evaluation_local import LocalEvaluationRunStore

            _evaluation_run_store = LocalEvaluationRunStore()
        else:
            _evaluation_run_store = EvaluationRunStore()
    return _evaluation_run_store


class EvaluationResultStore:
    """Per-case paired results partitioned by comparison ID."""

    def __init__(self):
        self._container = _get_container(
            settings.cosmos_evaluation_result_container
        )

    async def list(self, comparison_id: str) -> list[dict]:
        query = (
            "SELECT * FROM c WHERE c.comparison_id = @comparison_id "
            "ORDER BY c.case_id"
        )
        return [
            item
            async for item in self._container.query_items(
                query=query,
                parameters=[
                    {
                        "name": "@comparison_id",
                        "value": comparison_id,
                    }
                ],
                partition_key=comparison_id,
            )
        ]

    async def get(
        self,
        comparison_id: str,
        case_id: str,
    ) -> dict | None:
        try:
            return await self._container.read_item(
                item=case_id,
                partition_key=comparison_id,
            )
        except CosmosResourceNotFoundError:
            return None

    async def create(self, result: dict[str, Any]) -> dict:
        now = _now()
        item = {
            **result,
            "id": result["case_id"],
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        try:
            return await self._container.create_item(
                item,
                if_none_match="*",
            )
        except CosmosHttpResponseError as exc:
            if _is_conflict(exc):
                raise StoreConflictError(
                    "Evaluation result "
                    f"{item['comparison_id']}/{item['case_id']} exists"
                ) from exc
            raise StorePersistenceError(
                "Failed to create evaluation result "
                f"{item['comparison_id']}/{item['case_id']}: {exc}"
            ) from exc

    async def create_many(
        self,
        comparison_id: str,
        results: list[dict[str, Any]],
    ) -> list[dict]:
        now = _now()
        items = [
            {
                **result,
                "id": result["case_id"],
                "comparison_id": comparison_id,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
            for result in results
        ]
        operations = [("create", (item,)) for item in items]
        try:
            await self._container.execute_item_batch(
                batch_operations=operations,
                partition_key=comparison_id,
            )
        except (CosmosHttpResponseError, CosmosBatchOperationError) as exc:
            if getattr(exc, "status_code", None) in (409, 412):
                raise StoreConflictError(
                    f"Evaluation results for {comparison_id} already exist"
                ) from exc
            raise StorePersistenceError(
                f"Failed to create evaluation results for {comparison_id}: "
                f"{exc}"
            ) from exc
        return items

    async def update(
        self,
        comparison_id: str,
        case_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict:
        item = await self.get(comparison_id, case_id)
        if item is None:
            raise StoreNotFoundError(
                f"Evaluation result {comparison_id}/{case_id} not found"
            )
        if int(item.get("version", 1)) != expected_version:
            raise StoreConflictError(
                f"Evaluation result {comparison_id}/{case_id} was modified"
            )
        item.update(changes)
        item["id"] = case_id
        item["comparison_id"] = comparison_id
        item["case_id"] = case_id
        item["version"] = expected_version + 1
        item["updated_at"] = _now()
        try:
            return await self._container.replace_item(
                item=case_id,
                body=item,
                etag=item.get("_etag"),
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosHttpResponseError as exc:
            if _is_conflict(exc):
                raise StoreConflictError(
                    f"Evaluation result {comparison_id}/{case_id} was modified"
                ) from exc
            raise StorePersistenceError(
                "Failed to update evaluation result "
                f"{comparison_id}/{case_id}: {exc}"
            ) from exc

    async def update_scenario(
        self,
        comparison_id: str,
        case_id: str,
        scenario: str,
        scenario_result: dict[str, Any] | None,
        *,
        status: str,
        error: str = "",
        expected_version: int,
    ) -> dict:
        current = await self.get(comparison_id, case_id)
        if current is None:
            raise StoreNotFoundError(
                f"Evaluation result {comparison_id}/{case_id} not found"
            )
        scenarios = dict(current.get("scenarios") or {})
        scenario_statuses = dict(current.get("scenario_statuses") or {})
        scenario_errors = dict(current.get("scenario_errors") or {})
        scenarios[scenario] = scenario_result
        scenario_statuses[scenario] = status
        if error:
            scenario_errors[scenario] = error
        else:
            scenario_errors.pop(scenario, None)
        return await self.update(
            comparison_id,
            case_id,
            {
                "scenarios": scenarios,
                "scenario_statuses": scenario_statuses,
                "scenario_errors": scenario_errors,
            },
            expected_version=expected_version,
        )

    async def update_human_review(
        self,
        comparison_id: str,
        case_id: str,
        review: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict:
        return await self.update(
            comparison_id,
            case_id,
            {"human_review": review},
            expected_version=expected_version,
        )


def get_evaluation_result_store() -> EvaluationResultStore:
    global _evaluation_result_store
    if _evaluation_result_store is None:
        if settings.evaluation_mode == "stub":
            from app.services.evaluation_local import (
                LocalEvaluationResultStore,
            )

            _evaluation_result_store = LocalEvaluationResultStore()
        else:
            _evaluation_result_store = EvaluationResultStore()
    return _evaluation_result_store
