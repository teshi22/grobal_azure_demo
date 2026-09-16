"""Evaluation case import and seed helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import settings
from app.schemas.evaluation import EvaluationCaseCreate
from app.services.cosmos import EvaluationCaseStore, StoreConflictError


class CaseImportValidationError(ValueError):
    """One or more JSONL rows are invalid."""

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__("Evaluation case import contains invalid rows")


def parse_evaluation_cases_jsonl(content: str) -> list[EvaluationCaseCreate]:
    cases: list[EvaluationCaseCreate] = []
    errors: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(
                {
                    "line": line_number,
                    "message": f"Invalid JSON: {exc.msg}",
                }
            )
            continue
        try:
            cases.append(EvaluationCaseCreate.model_validate(row))
        except ValidationError as exc:
            errors.append(
                {
                    "line": line_number,
                    "message": "Invalid evaluation case",
                    "errors": exc.errors(include_url=False),
                }
            )
    if not cases and not errors:
        errors.append({"line": 0, "message": "No evaluation cases found"})
    if errors:
        raise CaseImportValidationError(errors)
    return cases


def load_seed_evaluation_cases(
    path: str | Path | None = None,
) -> list[EvaluationCaseCreate]:
    seed_path = Path(path or settings.evaluation_seed_path)
    return parse_evaluation_cases_jsonl(seed_path.read_text(encoding="utf-8"))


async def import_evaluation_cases(
    store: EvaluationCaseStore,
    cases: list[EvaluationCaseCreate],
    *,
    user_id: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "cases": [],
    }
    for case in cases:
        payload = case.model_dump(mode="json")
        existing = await store.get(case.id, case.dataset_id)
        if existing is None:
            try:
                saved = await store.create(payload, user_id)
            except StoreConflictError:
                if not overwrite:
                    result["skipped"] += 1
                    continue
                existing = await store.get(case.id, case.dataset_id)
                if existing is None:
                    raise
            else:
                result["created"] += 1
                result["cases"].append(saved)
                continue
        if not overwrite:
            result["skipped"] += 1
            continue
        saved = await store.update(
            case.id,
            case.dataset_id,
            payload,
            expected_version=int(existing.get("version", 1)),
            updated_by=user_id,
        )
        result["updated"] += 1
        result["cases"].append(saved)
    return result


async def seed_default_evaluation_cases(
    store: EvaluationCaseStore,
    *,
    user_id: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    return await import_evaluation_cases(
        store,
        load_seed_evaluation_cases(),
        user_id=user_id,
        overwrite=overwrite,
    )
