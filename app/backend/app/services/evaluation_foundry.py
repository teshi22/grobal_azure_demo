"""Foundry-backed paired agent evaluation with an explicit local stub."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from azure.ai.projects.models import TestingCriterionAzureAIEvaluator
from azure.core.exceptions import HttpResponseError
from openai import OpenAIError
from openai.types.eval_create_params import DataSourceConfigCustom

from app.config import settings
from app.schemas.evaluation import EvaluationCase, EvaluationScenario
from app.services.foundry import get_project_client

SCENARIOS: tuple[EvaluationScenario, ...] = (
    "agent_framework_workflow",
    "single_prompt_agent",
)
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
T = TypeVar("T")


class FoundryOperationError(RuntimeError):
    """A Foundry operation failed with a service or SDK error."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FoundrySdkUnsupportedError(FoundryOperationError):
    """The installed SDK does not expose the required evaluation API."""


@dataclass(frozen=True)
class FoundryRunSnapshot:
    status: str
    aggregate: dict[str, Any]
    rows: list[dict[str, Any]]
    per_model_usage: dict[str, dict[str, Any]]
    error: str = ""
    report_url: str = ""


class EvaluationAdapter(Protocol):
    def ensure_dataset(
        self,
        *,
        name: str,
        version: str,
        file_path: str,
    ) -> str: ...

    def ensure_rubric_evaluator(
        self,
        *,
        name: str,
        definition_hash: str,
        rubric: dict[str, Any],
    ) -> dict[str, str]: ...

    def ensure_evaluation(
        self,
        *,
        name: str,
        definition_hash: str,
        evaluator: dict[str, str],
    ) -> str: ...

    def create_run(
        self,
        *,
        evaluation_id: str,
        name: str,
        dataset_id: str,
        agent_name: str,
        agent_version: str,
        comparison_id: str,
        scenario: EvaluationScenario,
    ) -> dict[str, Any]: ...

    def cancel_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
    ) -> bool: ...

    def get_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
        cases: list[EvaluationCase],
        scenario: EvaluationScenario,
    ) -> FoundryRunSnapshot: ...

    def close(self) -> None: ...


class SdkFoundryEvaluationAdapter:
    """All preview/OpenAI evaluation shape conversion lives in this adapter."""

    def __init__(
        self,
        project_client: Any | None = None,
        openai_client: Any | None = None,
    ):
        self._project_client = project_client or get_project_client()
        self._openai_client = (
            openai_client or self._project_client.get_openai_client()
        )
        evals = getattr(self._openai_client, "evals", None)
        if evals is None or getattr(evals, "runs", None) is None:
            raise FoundrySdkUnsupportedError(
                "Installed azure-ai-projects/OpenAI SDK does not expose "
                "client.evals.runs"
            )
        beta = getattr(self._project_client, "beta", None)
        if beta is None or getattr(beta, "evaluators", None) is None:
            raise FoundrySdkUnsupportedError(
                "Installed azure-ai-projects SDK does not expose "
                "project_client.beta.evaluators"
            )

    @staticmethod
    def to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
        if hasattr(value, "as_dict"):
            return value.as_dict()
        if hasattr(value, "to_dict"):
            return value.to_dict()
        raise FoundrySdkUnsupportedError(
            f"Unsupported SDK response type: {type(value)!r}"
        )

    @staticmethod
    def _status_code(exc: BaseException) -> int | None:
        code = getattr(exc, "status_code", None)
        if isinstance(code, int):
            return code
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        return code if isinstance(code, int) else None

    @staticmethod
    def _retry_after(exc: BaseException) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        value = headers.get("retry-after")
        if value:
            try:
                return max(float(value), 0)
            except ValueError:
                return None
        value = headers.get("retry-after-ms")
        if value:
            try:
                return max(float(value) / 1000, 0)
            except ValueError:
                return None
        return None

    def _call(self, operation: str, callback: Callable[[], T]) -> T:
        attempts = max(settings.evaluation_retry_attempts, 1)
        for attempt in range(attempts):
            try:
                return callback()
            except (HttpResponseError, OpenAIError) as exc:
                status_code = self._status_code(exc)
                if status_code == 429 and attempt + 1 < attempts:
                    delay = self._retry_after(exc)
                    if delay is None:
                        delay = settings.evaluation_retry_base_seconds * (
                            2**attempt
                        )
                    time.sleep(delay)
                    continue
                raise FoundryOperationError(
                    f"{operation} failed: {exc}",
                    status_code=status_code,
                ) from exc
        raise AssertionError("retry loop exited unexpectedly")

    def ensure_dataset(
        self,
        *,
        name: str,
        version: str,
        file_path: str,
    ) -> str:
        try:
            existing = self._call(
                "Get evaluation dataset",
                lambda: self._project_client.datasets.get(name, version),
            )
        except FoundryOperationError as exc:
            if exc.status_code != 404:
                raise
        else:
            dataset_id = self.to_dict(existing).get("id")
            if not dataset_id:
                raise FoundrySdkUnsupportedError(
                    "Dataset response does not contain id"
                )
            return str(dataset_id)

        uploaded = self._call(
            "Upload evaluation dataset",
            lambda: self._project_client.datasets.upload_file(
                name=name,
                version=version,
                file_path=file_path,
            ),
        )
        dataset_id = self.to_dict(uploaded).get("id")
        if not dataset_id:
            raise FoundrySdkUnsupportedError(
                "Dataset upload response does not contain id"
            )
        return str(dataset_id)

    def ensure_rubric_evaluator(
        self,
        *,
        name: str,
        definition_hash: str,
        rubric: dict[str, Any],
    ) -> dict[str, str]:
        try:
            versions = self._call(
                "List rubric evaluator versions",
                lambda: list(
                    self._project_client.beta.evaluators.list_versions(name)
                ),
            )
        except FoundryOperationError as exc:
            if exc.status_code != 404:
                raise
            versions = []
        for version in versions:
            data = self.to_dict(version)
            tags = data.get("tags") or {}
            if tags.get("definition_hash") == definition_hash:
                return {
                    "name": str(data.get("name") or name),
                    "version": str(data.get("version") or ""),
                }

        dimensions = _normalized_rubric_dimensions(rubric)
        payload = {
            "evaluator_type": "custom",
            "categories": ["quality"],
            "display_name": rubric.get("name", "Travel request quality"),
            "description": "Travel request dual-scenario evaluation rubric",
            "definition": {
                "type": "rubric",
                "dimensions": dimensions,
                "pass_threshold": 0.5,
            },
            "tags": {"definition_hash": definition_hash},
        }
        try:
            created = self._call(
                "Create rubric evaluator",
                lambda: self._project_client.beta.evaluators.create_version(
                    name=name,
                    evaluator_version=payload,
                ),
            )
        except FoundryOperationError as exc:
            if exc.status_code != 409:
                raise
            versions = self._call(
                "Reload rubric evaluator versions",
                lambda: list(
                    self._project_client.beta.evaluators.list_versions(name)
                ),
            )
            if not versions:
                raise
            created = versions[0]
        data = self.to_dict(created)
        version = data.get("version")
        if not version:
            raise FoundrySdkUnsupportedError(
                "Rubric evaluator response does not contain version"
            )
        return {"name": str(data.get("name") or name), "version": str(version)}

    def ensure_evaluation(
        self,
        *,
        name: str,
        definition_hash: str,
        evaluator: dict[str, str],
    ) -> str:
        evaluations = self._call(
            "List evaluation definitions",
            lambda: list(self._openai_client.evals.list(limit=100)),
        )
        for evaluation in evaluations:
            data = self.to_dict(evaluation)
            if (data.get("metadata") or {}).get(
                "definition_hash"
            ) == definition_hash:
                evaluation_id = data.get("id")
                if evaluation_id:
                    return str(evaluation_id)

        testing_criteria = _testing_criteria(evaluator)
        created = self._call(
            "Create evaluation definition",
            lambda: self._openai_client.evals.create(
                name=name,
                metadata={"definition_hash": definition_hash},
                data_source_config=DataSourceConfigCustom(
                    type="custom",
                    item_schema=_evaluation_item_schema(),
                    include_sample_schema=True,
                ),
                testing_criteria=testing_criteria,
            ),
        )
        evaluation_id = self.to_dict(created).get("id")
        if not evaluation_id:
            raise FoundrySdkUnsupportedError(
                "Evaluation definition response does not contain id"
            )
        return str(evaluation_id)

    def create_run(
        self,
        *,
        evaluation_id: str,
        name: str,
        dataset_id: str,
        agent_name: str,
        agent_version: str,
        comparison_id: str,
        scenario: EvaluationScenario,
    ) -> dict[str, Any]:
        existing_runs = self._call(
            f"List {scenario} evaluation runs",
            lambda: list(
                self._openai_client.evals.runs.list(
                    evaluation_id,
                    limit=100,
                )
            ),
        )
        for existing in existing_runs:
            existing_data = self.to_dict(existing)
            metadata = existing_data.get("metadata") or {}
            if (
                metadata.get("comparison_id") == comparison_id
                and metadata.get("scenario") == scenario
            ):
                return _scenario_run_data(
                    existing_data,
                    agent_name=agent_name,
                    agent_version=agent_version,
                )

        created = self._call(
            f"Create {scenario} evaluation run",
            lambda: self._openai_client.evals.runs.create(
                eval_id=evaluation_id,
                name=name,
                metadata={
                    "comparison_id": comparison_id,
                    "scenario": scenario,
                },
                extra_headers={
                    "Idempotency-Key": (
                        f"evaluation-{comparison_id}-{scenario}"
                    )
                },
                data_source={
                    "type": "azure_ai_target_completions",
                    "source": {"type": "file_id", "id": dataset_id},
                    "input_messages": {
                        "type": "template",
                        "template": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": {
                                    "type": "input_text",
                                    "text": (
                                        "{{item.evaluation_envelope}}"
                                    ),
                                },
                            }
                        ],
                    },
                    "target": {
                        "type": "azure_ai_agent",
                        "name": agent_name,
                        "version": agent_version,
                    },
                },
            ),
        )
        data = self.to_dict(created)
        return _scenario_run_data(
            data,
            agent_name=agent_name,
            agent_version=agent_version,
        )

    def cancel_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
    ) -> bool:
        cancel = getattr(self._openai_client.evals.runs, "cancel", None)
        if not callable(cancel):
            return False
        self._call(
            "Cancel evaluation run",
            lambda: cancel(run_id=run_id, eval_id=evaluation_id),
        )
        return True

    def get_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
        cases: list[EvaluationCase],
        scenario: EvaluationScenario,
    ) -> FoundryRunSnapshot:
        run = self._call(
            f"Get {scenario} evaluation run",
            lambda: self._openai_client.evals.runs.retrieve(
                run_id=run_id,
                eval_id=evaluation_id,
            ),
        )
        data = self.to_dict(run)
        status = str(data.get("status") or "unknown")
        rows: list[dict[str, Any]] = []
        if status == "completed":
            output_items = self._call(
                f"List {scenario} evaluation rows",
                lambda: list(
                    self._openai_client.evals.runs.output_items.list(
                        run_id=run_id,
                        eval_id=evaluation_id,
                    )
                ),
            )
            rows = [self._normalize_row(item) for item in output_items]
        aggregate = {
            "result_counts": data.get("result_counts") or {},
            "per_testing_criteria_results": (
                data.get("per_testing_criteria_results") or []
            ),
        }
        usage = _normalize_per_model_usage(data.get("per_model_usage") or [])
        return FoundryRunSnapshot(
            status=status,
            aggregate=aggregate,
            rows=rows,
            per_model_usage=usage,
            error=_error_text(data.get("error")),
            report_url=str(data.get("report_url") or ""),
        )

    def _normalize_row(self, value: Any) -> dict[str, Any]:
        row = self.to_dict(value)
        datasource = row.get("datasource_item") or {}
        if not isinstance(datasource, dict):
            raise FoundrySdkUnsupportedError(
                "Evaluation row datasource_item is not an object"
            )
        case_id = datasource.get("case_id")
        if not case_id:
            item = datasource.get("item")
            if isinstance(item, dict):
                case_id = item.get("case_id")
        if not case_id:
            raise FoundrySdkUnsupportedError(
                "Evaluation row does not contain datasource_item.case_id"
            )
        raw_output = datasource.get("sample.output_text")
        sample = row.get("sample") or {}
        if raw_output is None and isinstance(sample, dict):
            output = sample.get("output") or []
            contents = [
                str(entry.get("content"))
                for entry in output
                if isinstance(entry, dict) and entry.get("content") is not None
            ]
            if contents:
                raw_output = "\n".join(contents)
        results = row.get("results") or []
        evaluator_results = [
            self.to_dict(result) if not isinstance(result, dict) else result
            for result in results
        ]
        return {
            "case_id": str(case_id),
            "raw_output": raw_output,
            "evaluator_results": evaluator_results,
            "sample": sample if isinstance(sample, dict) else {},
            "status": str(row.get("status") or "completed"),
        }

    def close(self) -> None:
        close = getattr(self._openai_client, "close", None)
        if callable(close):
            close()


class StubFoundryEvaluationAdapter:
    """Deterministic local adapter enabled only through EVALUATION_MODE=stub."""

    def ensure_dataset(
        self,
        *,
        name: str,
        version: str,
        file_path: str,
    ) -> str:
        return f"stub-dataset:{name}:{version}"

    def ensure_rubric_evaluator(
        self,
        *,
        name: str,
        definition_hash: str,
        rubric: dict[str, Any],
    ) -> dict[str, str]:
        return {"name": name, "version": definition_hash[:12]}

    def ensure_evaluation(
        self,
        *,
        name: str,
        definition_hash: str,
        evaluator: dict[str, str],
    ) -> str:
        return f"stub-eval:{definition_hash[:16]}"

    def create_run(
        self,
        *,
        evaluation_id: str,
        name: str,
        dataset_id: str,
        agent_name: str,
        agent_version: str,
        comparison_id: str,
        scenario: EvaluationScenario,
    ) -> dict[str, Any]:
        return {
            "id": f"stub-run:{comparison_id}:{scenario}",
            "status": "completed",
            "error": "",
            "results_synced": False,
            "agent_name": agent_name,
            "agent_version": agent_version or "stub",
        }

    def cancel_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
    ) -> bool:
        return True

    def get_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
        cases: list[EvaluationCase],
        scenario: EvaluationScenario,
    ) -> FoundryRunSnapshot:
        score = 0.92 if scenario == "agent_framework_workflow" else 0.84
        rows = [
            {
                "case_id": case.id,
                "raw_output": _stub_output(case, scenario),
                "evaluator_results": [
                    {
                        "name": "travel_request_quality",
                        "score": score,
                        "passed": True,
                        "reason": "Deterministic local evaluation stub",
                    }
                ],
                "sample": {
                    "model": settings.evaluation_judge_model,
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    },
                },
                "status": "completed",
            }
            for case in cases
        ]
        usage = {
            settings.evaluation_judge_model: {
                "prompt_tokens": len(cases) * 100,
                "completion_tokens": len(cases) * 50,
                "total_tokens": len(cases) * 150,
                "invocation_count": len(cases),
            }
        }
        return FoundryRunSnapshot(
            status="completed",
            aggregate={
                "result_counts": {
                    "total": len(cases),
                    "passed": len(cases),
                    "failed": 0,
                    "errored": 0,
                },
                "per_testing_criteria_results": [
                    {
                        "testing_criteria": "travel_request_quality",
                        "passed": len(cases),
                        "failed": 0,
                    }
                ],
            },
            rows=rows,
            per_model_usage=usage,
            report_url="",
        )

    def close(self) -> None:
        return None


class EvaluationFoundryService:
    def __init__(self, adapter: EvaluationAdapter):
        self._adapter = adapter

    async def prepare_comparison(
        self,
        *,
        cases: list[EvaluationCase],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._prepare_comparison_sync,
            cases,
        )

    def _prepare_comparison_sync(
        self,
        cases: list[EvaluationCase],
    ) -> dict[str, Any]:
        if not cases:
            raise ValueError("At least one evaluation case is required")
        if settings.evaluation_mode == "foundry":
            missing_versions = [
                setting_name
                for setting_name, value in (
                    ("HOSTED_AGENT_VERSION", settings.hosted_agent_version),
                    (
                        "SINGLE_PROMPT_AGENT_VERSION",
                        settings.single_prompt_agent_version,
                    ),
                )
                if not value
            ]
            if missing_versions:
                raise FoundryOperationError(
                    "Pinned agent versions are required: "
                    + ", ".join(missing_versions)
                )
        rubric = _load_rubric()
        rows = [_dataset_row(case) for case in cases]
        dataset_hash = _stable_hash(rows)
        rubric_hash = _stable_hash({"rubric": rubric})
        dataset_name = (
            "evaluation-"
            + re.sub(r"[^a-zA-Z0-9_-]", "-", cases[0].dataset_id)[:80]
        )
        dataset_version = dataset_hash[:16]
        temp_dir = Path(settings.evaluation_temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"{uuid.uuid4().hex}.jsonl"
        try:
            temp_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                    for row in rows
                ),
                encoding="utf-8",
            )
            dataset_id = self._adapter.ensure_dataset(
                name=dataset_name,
                version=dataset_version,
                file_path=str(temp_path),
            )
        finally:
            temp_path.unlink(missing_ok=True)
            try:
                temp_dir.rmdir()
            except OSError:
                pass

        evaluator_name = f"travel-request-quality-{rubric_hash[:16]}"
        evaluator = self._adapter.ensure_rubric_evaluator(
            name=evaluator_name,
            definition_hash=rubric_hash,
            rubric=rubric,
        )
        definition_hash = _stable_hash(
            _evaluation_definition_contract(evaluator)
        )
        evaluation_id = self._adapter.ensure_evaluation(
            name=f"travel-request-evaluation-{definition_hash[:16]}",
            definition_hash=definition_hash,
            evaluator=evaluator,
        )
        return {
            "dataset_id": cases[0].dataset_id,
            "dataset_version": dataset_version,
            "foundry_dataset_id": dataset_id,
            "foundry_evaluation_id": evaluation_id,
            "evaluation_definition_hash": definition_hash,
            "scenario_runs": {
                scenario: {
                    "id": "",
                    "status": "pending_launch",
                    "error": "",
                    "results_synced": False,
                    "agent_name": _scenario_target(scenario)[0],
                    "agent_version": _scenario_target(scenario)[1],
                }
                for scenario in SCENARIOS
            },
        }

    async def launch_run(
        self,
        *,
        prepared: dict[str, Any],
        comparison_id: str,
        name: str,
        scenario: EvaluationScenario,
    ) -> dict[str, Any]:
        agent_name, agent_version = _scenario_target(scenario)
        return await asyncio.to_thread(
            self._adapter.create_run,
            evaluation_id=prepared["foundry_evaluation_id"],
            name=f"{name or comparison_id} - {scenario}",
            dataset_id=prepared["foundry_dataset_id"],
            agent_name=agent_name,
            agent_version=agent_version,
            comparison_id=comparison_id,
            scenario=scenario,
        )

    async def cancel_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._adapter.cancel_run,
            evaluation_id=evaluation_id,
            run_id=run_id,
        )

    async def get_run(
        self,
        *,
        evaluation_id: str,
        run_id: str,
        cases: list[EvaluationCase],
        scenario: EvaluationScenario,
    ) -> FoundryRunSnapshot:
        return await asyncio.to_thread(
            self._adapter.get_run,
            evaluation_id=evaluation_id,
            run_id=run_id,
            cases=cases,
            scenario=scenario,
        )

    def close(self) -> None:
        self._adapter.close()


def _scenario_target(
    scenario: EvaluationScenario,
) -> tuple[str, str]:
    if scenario == "agent_framework_workflow":
        return settings.hosted_agent_name, settings.hosted_agent_version
    return (
        settings.single_prompt_agent_name,
        settings.single_prompt_agent_version,
    )


def _evaluation_item_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "case_id": {"type": "string"},
            "evaluation_envelope": {"type": "string"},
            "query": {"type": "string"},
            "ground_truth": {"type": "string"},
        },
        "required": [
            "case_id",
            "evaluation_envelope",
            "query",
            "ground_truth",
        ],
    }


def _testing_criteria(
    evaluator: dict[str, str],
) -> list[dict[str, Any]]:
    return [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="travel_request_quality",
            evaluator_name=evaluator["name"],
            evaluator_version=evaluator["version"],
            initialization_parameters={
                "deployment_name": settings.evaluation_judge_model
            },
            data_mapping={
                "query": "{{item.query}}",
                "response": "{{sample.output_text}}",
            },
        )
    ]


def _evaluation_definition_contract(
    evaluator: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "item_schema": _evaluation_item_schema(),
        "testing_criteria": _testing_criteria(evaluator),
    }


def _dataset_row(case: EvaluationCase) -> dict[str, Any]:
    envelope = {
        "mode": "evaluation",
        "schema_version": "1",
        "case_id": case.id,
        "input": case.input,
    }
    ground_truth = {
        "expected_status": case.expected_status,
        "expected_request": case.expected_request.model_dump(mode="json"),
        "expected_policy_compliant": case.expected_policy_compliant,
        "expected_clarification_fields": case.expected_clarification_fields,
    }
    return {
        "case_id": case.id,
        "evaluation_envelope": json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "query": case.input,
        "ground_truth": json.dumps(
            ground_truth,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "tags": case.tags,
        "case_version": case.version,
    }


def _stable_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_rubric() -> dict[str, Any]:
    path = Path(settings.evaluation_rubric_path)
    with path.open(encoding="utf-8") as file:
        rubric = json.load(file)
    if not isinstance(rubric.get("dimensions"), list):
        raise ValueError("Evaluation rubric requires dimensions")
    return rubric


def _normalized_rubric_dimensions(
    rubric: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_dimensions = rubric["dimensions"]
    raw_weights = [int(item["weight"]) for item in raw_dimensions]
    divisor = _greatest_common_divisor(raw_weights)
    dimensions = []
    for item, weight in zip(raw_dimensions, raw_weights, strict=True):
        normalized_weight = max(1, min(10, round(weight / divisor)))
        dimensions.append(
            {
                "id": str(item["name"]),
                "description": str(item["description"]),
                "weight": normalized_weight,
                "always_applicable": True,
            }
        )
    return dimensions


def _greatest_common_divisor(values: list[int]) -> int:
    from math import gcd

    result = 0
    for value in values:
        result = gcd(result, value)
    return max(result, 1)


def _normalize_per_model_usage(
    usage_rows: list[Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in usage_rows:
        row = (
            SdkFoundryEvaluationAdapter.to_dict(raw)
            if not isinstance(raw, dict)
            else raw
        )
        model = row.get("model_name") or row.get("run_model_name")
        if model:
            result[str(model)] = {
                key: value
                for key, value in row.items()
                if key not in {"model_name", "run_model_name"}
            }
    return result


def _scenario_run_data(
    data: dict[str, Any],
    *,
    agent_name: str,
    agent_version: str,
) -> dict[str, Any]:
    run_id = data.get("id")
    if not run_id:
        raise FoundrySdkUnsupportedError(
            "Evaluation run response does not contain id"
        )
    return {
        "id": str(run_id),
        "status": str(data.get("status") or "queued"),
        "error": _error_text(data.get("error")),
        "results_synced": False,
        "agent_name": agent_name,
        "agent_version": agent_version,
    }


def _error_text(error: Any) -> str:
    if not error:
        return ""
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return str(error.get("message") or json.dumps(error, default=str))
    return str(error)


def _stub_output(
    case: EvaluationCase,
    scenario: EvaluationScenario,
) -> dict[str, Any]:
    diagnostics = {
        "schema_version": "1",
        "scenario": scenario,
        "case_id": case.id,
        "agent_versions": {
            "hosted": settings.hosted_agent_version or "stub",
            "single": settings.single_prompt_agent_version or "stub",
        },
    }
    if case.expected_status == "needs_clarification":
        questions = [
            f"{field}を教えてください。"
            for field in case.expected_clarification_fields
        ]
        return {
            "status": "needs_clarification",
            "request": case.expected_request.model_dump(mode="json"),
            "clarification_questions": questions,
            "itinerary": None,
            "fare_total": None,
            "policy": {"compliant": None, "details": [], "narrative": ""},
            "application_draft": "",
            "citations": [],
            "diagnostics": diagnostics,
        }
    return {
        "status": case.expected_status,
        "request": case.expected_request.model_dump(mode="json"),
        "clarification_questions": [],
        "itinerary": {
            **case.expected_request.model_dump(mode="json"),
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往復",
                    "method": "鉄道",
                    "from": case.expected_request.departure,
                    "to": case.expected_request.destination,
                    "cost": 10000,
                    "fare_type": "通常運賃",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                    "source_title": "スマートEX",
                }
            ],
            "transportation_cost": 10000,
            "total_cost": 10000,
        },
        "fare_total": 10000,
        "policy": {
            "compliant": case.expected_policy_compliant,
            "details": [],
            "narrative": "規程に適合しています。",
        },
        "application_draft": f"{case.title}の出張申請案です。",
        "citations": [],
        "diagnostics": diagnostics,
    }


_evaluation_service: EvaluationFoundryService | None = None


def get_evaluation_service() -> EvaluationFoundryService:
    global _evaluation_service
    if _evaluation_service is None:
        if (
            settings.evaluation_mode == "stub"
            and settings.app_environment.casefold() in {"production", "prod"}
        ):
            raise RuntimeError(
                "EVALUATION_MODE=stub is not allowed in production"
            )
        adapter: EvaluationAdapter
        if settings.evaluation_mode == "stub":
            adapter = StubFoundryEvaluationAdapter()
        else:
            adapter = SdkFoundryEvaluationAdapter()
        _evaluation_service = EvaluationFoundryService(adapter)
    return _evaluation_service


def close_evaluation_service() -> None:
    global _evaluation_service
    if _evaluation_service is not None:
        _evaluation_service.close()
        _evaluation_service = None
