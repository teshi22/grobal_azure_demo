"""In-memory evaluation stores for the explicit local stub mode."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any

from app.services.cosmos import StoreConflictError, StoreNotFoundError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalEvaluationCaseStore:
    def __init__(self):
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def list(
        self,
        dataset_id: str,
        *,
        enabled: bool | None = None,
    ) -> list[dict]:
        items = [
            copy.deepcopy(item)
            for (dataset, _), item in self._items.items()
            if dataset == dataset_id
            and (enabled is None or item.get("enabled") == enabled)
        ]
        return sorted(items, key=lambda item: item["id"])

    async def get(self, case_id: str, dataset_id: str) -> dict | None:
        return copy.deepcopy(self._items.get((dataset_id, case_id)))

    async def create(self, case: dict[str, Any], creator_id: str) -> dict:
        key = (str(case["dataset_id"]), str(case["id"]))
        async with self._lock:
            if key in self._items:
                raise StoreConflictError(
                    f"Evaluation case {case['id']} already exists"
                )
            now = _now()
            item = {
                **copy.deepcopy(case),
                "version": 1,
                "created_by": creator_id,
                "updated_by": creator_id,
                "created_at": now,
                "updated_at": now,
            }
            self._items[key] = item
            return copy.deepcopy(item)

    async def update(
        self,
        case_id: str,
        dataset_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int,
        updated_by: str,
    ) -> dict:
        key = (dataset_id, case_id)
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                raise StoreNotFoundError(
                    f"Evaluation case {case_id} not found"
                )
            if item["version"] != expected_version:
                raise StoreConflictError(
                    f"Evaluation case {case_id} was modified"
                )
            item.update(copy.deepcopy(changes))
            item["id"] = case_id
            item["dataset_id"] = dataset_id
            item["version"] += 1
            item["updated_by"] = updated_by
            item["updated_at"] = _now()
            return copy.deepcopy(item)


class LocalEvaluationRunStore:
    def __init__(self):
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def list(self, limit: int = 50) -> list[dict]:
        items = sorted(
            self._items.values(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
        return copy.deepcopy(items[:limit])

    async def get(self, comparison_id: str) -> dict | None:
        return copy.deepcopy(self._items.get(comparison_id))

    async def create(self, run: dict[str, Any], creator_id: str) -> dict:
        comparison_id = str(run["id"])
        async with self._lock:
            if comparison_id in self._items:
                raise StoreConflictError(
                    f"Evaluation run {comparison_id} already exists"
                )
            now = _now()
            item = {
                **copy.deepcopy(run),
                "version": 1,
                "created_by": creator_id,
                "created_at": now,
                "updated_at": now,
            }
            self._items[comparison_id] = item
            return copy.deepcopy(item)

    async def update(
        self,
        comparison_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict:
        async with self._lock:
            item = self._items.get(comparison_id)
            if item is None:
                raise StoreNotFoundError(
                    f"Evaluation run {comparison_id} not found"
                )
            if item["version"] != expected_version:
                raise StoreConflictError(
                    f"Evaluation run {comparison_id} was modified"
                )
            item.update(copy.deepcopy(changes))
            item["version"] += 1
            item["updated_at"] = _now()
            return copy.deepcopy(item)


class LocalEvaluationResultStore:
    def __init__(self):
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def list(self, comparison_id: str) -> list[dict]:
        items = [
            copy.deepcopy(item)
            for (run_id, _), item in self._items.items()
            if run_id == comparison_id
        ]
        return sorted(items, key=lambda item: item["case_id"])

    async def get(
        self,
        comparison_id: str,
        case_id: str,
    ) -> dict | None:
        return copy.deepcopy(self._items.get((comparison_id, case_id)))

    async def create(self, result: dict[str, Any]) -> dict:
        key = (str(result["comparison_id"]), str(result["case_id"]))
        async with self._lock:
            if key in self._items:
                raise StoreConflictError(
                    f"Evaluation result {key[0]}/{key[1]} exists"
                )
            now = _now()
            item = {
                **copy.deepcopy(result),
                "id": key[1],
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
            self._items[key] = item
            return copy.deepcopy(item)

    async def create_many(
        self,
        comparison_id: str,
        results: list[dict[str, Any]],
    ) -> list[dict]:
        async with self._lock:
            keys = [
                (comparison_id, str(result["case_id"]))
                for result in results
            ]
            if any(key in self._items for key in keys):
                raise StoreConflictError(
                    f"Evaluation results for {comparison_id} already exist"
                )
            now = _now()
            saved = []
            for key, result in zip(keys, results, strict=True):
                item = {
                    **copy.deepcopy(result),
                    "id": key[1],
                    "comparison_id": comparison_id,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                self._items[key] = item
                saved.append(copy.deepcopy(item))
            return saved

    async def update(
        self,
        comparison_id: str,
        case_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict:
        key = (comparison_id, case_id)
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                raise StoreNotFoundError(
                    f"Evaluation result {comparison_id}/{case_id} not found"
                )
            if item["version"] != expected_version:
                raise StoreConflictError(
                    f"Evaluation result {comparison_id}/{case_id} was modified"
                )
            item.update(copy.deepcopy(changes))
            item["version"] += 1
            item["updated_at"] = _now()
            return copy.deepcopy(item)

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
        item = await self.get(comparison_id, case_id)
        if item is None:
            raise StoreNotFoundError(
                f"Evaluation result {comparison_id}/{case_id} not found"
            )
        scenarios = dict(item.get("scenarios") or {})
        statuses = dict(item.get("scenario_statuses") or {})
        errors = dict(item.get("scenario_errors") or {})
        scenarios[scenario] = scenario_result
        statuses[scenario] = status
        if error:
            errors[scenario] = error
        else:
            errors.pop(scenario, None)
        return await self.update(
            comparison_id,
            case_id,
            {
                "scenarios": scenarios,
                "scenario_statuses": statuses,
                "scenario_errors": errors,
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
