import asyncio
import copy

import pytest
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from app.services.cosmos import (
    ConversationStore,
    EvaluationCaseStore,
    EvaluationResultStore,
    StoreConflictError,
    TravelRequestStore,
)


class _ConversationContainer:
    def __init__(self):
        self.item: dict | None = None

    async def create_item(self, body):
        self.item = copy.deepcopy(body)
        return copy.deepcopy(body)

    async def read_item(self, item, partition_key):
        if (
            self.item is None
            or self.item["id"] != item
            or partition_key != item
        ):
            raise CosmosResourceNotFoundError(status_code=404)
        return copy.deepcopy(self.item)


def test_conversation_store_persists_route_and_defaults_legacy_documents():
    container = _ConversationContainer()
    store = ConversationStore.__new__(ConversationStore)
    store._container = container

    created = asyncio.run(
        store.create(
            "conversation-1",
            "user-1",
            scenario="single_prompt_agent",
        )
    )

    assert created["scenario"] == "single_prompt_agent"
    assert "interaction_mode" not in created
    assert "submission_token" not in created

    container.item = {
        "id": "legacy-conversation",
        "user_id": "user-1",
        "status": "created",
    }
    legacy = asyncio.run(store.get("legacy-conversation"))
    assert legacy["scenario"] == "agent_framework_workflow"


class _AsyncItems:
    def __init__(self, items: list[dict]):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Container:
    def __init__(self):
        self.query_kwargs: dict | None = None

    def query_items(self, **kwargs):
        self.query_kwargs = kwargs
        return _AsyncItems([{"request_id": "TR-1"}])


def test_list_by_user_uses_async_cosmos_query_without_sync_only_options():
    container = _Container()
    store = TravelRequestStore.__new__(TravelRequestStore)
    store._container = container

    items = asyncio.run(store.list_by_user("user-1", limit=25))

    assert items == [{"request_id": "TR-1"}]
    assert container.query_kwargs == {
        "query": (
            "SELECT TOP @limit * FROM c WHERE c.user_id = @user_id "
            "ORDER BY c.submitted_at DESC"
        ),
        "parameters": [
            {"name": "@limit", "value": 25},
            {"name": "@user_id", "value": "user-1"},
        ],
    }


class _DocumentContainer:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self.etag = 0
        self.batch_kwargs: dict | None = None

    def _key(self, body: dict) -> tuple[str, str]:
        partition = body.get("dataset_id") or body.get("comparison_id")
        return str(partition), str(body["id"])

    async def create_item(self, body, if_none_match=None):
        key = self._key(body)
        if key in self.items:
            raise CosmosHttpResponseError(status_code=409)
        self.etag += 1
        stored = {**copy.deepcopy(body), "_etag": str(self.etag)}
        self.items[key] = stored
        return copy.deepcopy(stored)

    async def read_item(self, item, partition_key):
        key = str(partition_key), str(item)
        if key not in self.items:
            raise CosmosResourceNotFoundError(status_code=404)
        return copy.deepcopy(self.items[key])

    async def replace_item(
        self,
        item,
        body,
        etag=None,
        match_condition=None,
    ):
        key = self._key(body)
        current = self.items.get(key)
        if current is None:
            raise CosmosResourceNotFoundError(status_code=404)
        if etag != current["_etag"]:
            raise CosmosHttpResponseError(status_code=412)
        self.etag += 1
        stored = {**copy.deepcopy(body), "_etag": str(self.etag)}
        self.items[key] = stored
        return copy.deepcopy(stored)

    async def execute_item_batch(self, **kwargs):
        self.batch_kwargs = copy.deepcopy(kwargs)
        partition_key = str(kwargs["partition_key"])
        operations = kwargs["batch_operations"]
        pending = []
        for operation, arguments in operations:
            assert operation == "create"
            body = arguments[0]
            key = partition_key, str(body["id"])
            if key in self.items:
                raise CosmosHttpResponseError(status_code=409)
            pending.append((key, body))
        for key, body in pending:
            self.etag += 1
            self.items[key] = {
                **copy.deepcopy(body),
                "_etag": str(self.etag),
            }


def test_evaluation_case_store_create_update_and_conflict():
    container = _DocumentContainer()
    store = EvaluationCaseStore.__new__(EvaluationCaseStore)
    store._container = container
    case = {
        "id": "case-001",
        "dataset_id": "travel-request-v1",
        "title": "Case",
        "input": "Input",
        "expected_status": "draft_ready",
    }

    created = asyncio.run(store.create(case, "creator"))
    updated = asyncio.run(
        store.update(
            "case-001",
            "travel-request-v1",
            {"title": "Updated"},
            expected_version=created["version"],
            updated_by="editor",
        )
    )

    assert updated["version"] == 2
    assert updated["created_by"] == "creator"
    assert updated["updated_by"] == "editor"
    with pytest.raises(StoreConflictError):
        asyncio.run(
            store.update(
                "case-001",
                "travel-request-v1",
                {"title": "Stale"},
                expected_version=1,
                updated_by="editor",
            )
        )


def test_evaluation_result_store_preserves_shared_review_on_scenario_update():
    container = _DocumentContainer()
    store = EvaluationResultStore.__new__(EvaluationResultStore)
    store._container = container
    created = asyncio.run(
        store.create(
            {
                "comparison_id": "comparison-1",
                "case_id": "case-001",
                "scenarios": {
                    "agent_framework_workflow": None,
                    "single_prompt_agent": None,
                },
                "human_review": {"winner": "tie"},
            }
        )
    )

    updated = asyncio.run(
        store.update_scenario(
            "comparison-1",
            "case-001",
            "agent_framework_workflow",
            {"overall_score": 90},
            status="completed",
            expected_version=created["version"],
        )
    )

    assert updated["human_review"] == {"winner": "tie"}
    assert updated["scenarios"]["agent_framework_workflow"] == {
        "overall_score": 90
    }


def test_evaluation_result_store_creates_one_partition_batch_atomically():
    container = _DocumentContainer()
    store = EvaluationResultStore.__new__(EvaluationResultStore)
    store._container = container
    results = [
        {
            "comparison_id": "comparison-1",
            "case_id": f"case-{index}",
            "case_snapshot": {"id": f"case-{index}"},
        }
        for index in range(3)
    ]

    created = asyncio.run(store.create_many("comparison-1", results))

    assert len(created) == 3
    assert container.batch_kwargs["partition_key"] == "comparison-1"
    assert len(container.batch_kwargs["batch_operations"]) == 3
    assert set(container.items) == {
        ("comparison-1", "case-0"),
        ("comparison-1", "case-1"),
        ("comparison-1", "case-2"),
    }

    with pytest.raises(StoreConflictError):
        asyncio.run(
            store.create_many(
                "comparison-1",
                [
                    {
                        "comparison_id": "comparison-1",
                        "case_id": "case-new",
                    },
                    {
                        "comparison_id": "comparison-1",
                        "case_id": "case-1",
                    },
                ],
            )
        )
    assert ("comparison-1", "case-new") not in container.items
