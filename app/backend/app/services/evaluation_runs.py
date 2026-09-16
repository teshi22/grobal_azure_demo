"""Comparison run orchestration, synchronization, and scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunCreate,
    EvaluationScenario,
    EVALUATION_DATASET_ID,
    MAX_EVALUATION_CASES_PER_RUN,
    HumanReview,
    HumanReviewUpdate,
)
from app.services.cosmos import (
    EvaluationCaseStore,
    EvaluationResultStore,
    EvaluationRunStore,
    StoreConflictError,
    StoreNotFoundError,
    StorePersistenceError,
)
from app.services.evaluation_foundry import (
    SCENARIOS,
    TERMINAL_STATUSES,
    EvaluationFoundryService,
    FoundryOperationError,
    FoundryRunSnapshot,
)
from app.services.evaluation_scoring import (
    composite_score,
    estimate_token_cost,
    load_model_pricing,
    score_evaluation_output,
)


class EvaluationSelectionError(ValueError):
    """The requested case selection is empty or contains unknown cases."""


class EvaluationRunCoordinator:
    def __init__(
        self,
        *,
        case_store: EvaluationCaseStore,
        run_store: EvaluationRunStore,
        result_store: EvaluationResultStore,
        foundry_service: EvaluationFoundryService,
        pricing: dict[str, Any] | None = None,
    ):
        self._cases = case_store
        self._runs = run_store
        self._results = result_store
        self._foundry = foundry_service
        self._pricing = pricing or load_model_pricing()

    async def create_run(
        self,
        *,
        comparison_id: str,
        request: EvaluationRunCreate,
        creator_id: str,
    ) -> EvaluationRun:
        cases = await self._select_cases(request)
        prepared = await self._foundry.prepare_comparison(
            cases=cases,
        )
        scenario_runs = prepared["scenario_runs"]
        run_item = await self._runs.create(
            {
                "id": comparison_id,
                "name": request.name or f"Comparison {comparison_id[:8]}",
                "status": "preparing",
                "dataset_id": prepared["dataset_id"],
                "dataset_version": prepared["dataset_version"],
                "foundry_dataset_id": prepared["foundry_dataset_id"],
                "foundry_evaluation_id": prepared[
                    "foundry_evaluation_id"
                ],
                "evaluation_definition_hash": prepared[
                    "evaluation_definition_hash"
                ],
                "scenario_runs": scenario_runs,
                "case_ids": [case.id for case in cases],
                "result_documents_state": "pending",
                "aggregate": {},
                "pricing_updated_at": str(
                    self._pricing.get("updated_at") or ""
                ),
                "error": "",
            },
            creator_id,
        )
        result_documents = [
            _result_document(comparison_id, case, scenario_runs)
            for case in cases
        ]
        try:
            await self._results.create_many(
                comparison_id,
                result_documents,
            )
        except (StoreConflictError, StorePersistenceError) as exc:
            failed = await self._update_run_with_retry(
                comparison_id,
                {
                    "status": "failed",
                    "result_documents_state": "failed",
                    "error": f"Result document persistence failed: {exc}",
                    "scenario_runs": _fail_unlaunched_scenarios(
                        scenario_runs,
                        "Result documents were not persisted",
                    ),
                },
            )
            return EvaluationRun.model_validate(failed)
        run_item = await self._update_run_with_retry(
            comparison_id,
            {
                "status": "launching",
                "result_documents_state": "ready",
            },
        )
        launched = await self._launch_pending_runs(run_item, cases)
        return EvaluationRun.model_validate(launched)

    async def _select_cases(
        self,
        request: EvaluationRunCreate,
    ) -> list[EvaluationCase]:
        if request.dataset_id != EVALUATION_DATASET_ID:
            raise EvaluationSelectionError(
                f"Only dataset {EVALUATION_DATASET_ID} is supported"
            )
        if not request.case_ids:
            items = await self._cases.list(
                EVALUATION_DATASET_ID,
                enabled=True,
            )
            cases = [EvaluationCase.model_validate(item) for item in items]
        else:
            cases = []
            missing = []
            for case_id in request.case_ids:
                item = await self._cases.get(
                    case_id,
                    EVALUATION_DATASET_ID,
                )
                if item is None or not item.get("enabled", True):
                    missing.append(case_id)
                else:
                    cases.append(EvaluationCase.model_validate(item))
            if missing:
                raise EvaluationSelectionError(
                    "Unknown or disabled evaluation cases: "
                    + ", ".join(missing)
                )
        if not cases:
            raise EvaluationSelectionError(
                f"No enabled cases found for dataset {EVALUATION_DATASET_ID}"
            )
        if len(cases) > MAX_EVALUATION_CASES_PER_RUN:
            raise EvaluationSelectionError(
                "Evaluation run exceeds the "
                f"{MAX_EVALUATION_CASES_PER_RUN}-case limit"
            )
        return cases

    async def sync_run(self, comparison_id: str) -> EvaluationRun:
        run_item = await self._runs.get(comparison_id)
        if run_item is None:
            raise StoreNotFoundError(
                f"Evaluation run {comparison_id} not found"
            )
        if run_item.get("result_documents_state") == "failed":
            return EvaluationRun.model_validate(run_item)
        cases = await self._load_persisted_cases(run_item)
        if run_item.get("result_documents_state") != "ready":
            run_item = await self._update_run_with_retry(
                comparison_id,
                {"result_documents_state": "ready"},
            )
        if any(
            not (run_item.get("scenario_runs") or {})
            .get(scenario, {})
            .get("id")
            and (
                (run_item.get("scenario_runs") or {})
                .get(scenario, {})
                .get("status")
                in {"pending_launch", "launching"}
            )
            for scenario in SCENARIOS
        ):
            run_item = await self._launch_pending_runs(run_item, cases)
        scenario_runs = {
            key: dict(value)
            for key, value in (run_item.get("scenario_runs") or {}).items()
        }
        aggregate = dict(run_item.get("aggregate") or {})
        aggregate_scenarios = dict(aggregate.get("scenarios") or {})

        for scenario in SCENARIOS:
            scenario_run = scenario_runs.get(scenario) or {}
            run_id = str(scenario_run.get("id") or "")
            if scenario_run.get("results_synced") or not run_id:
                continue
            try:
                snapshot = await self._foundry.get_run(
                    evaluation_id=run_item["foundry_evaluation_id"],
                    run_id=run_id,
                    cases=cases,
                    scenario=scenario,
                )
            except FoundryOperationError as exc:
                scenario_run["last_sync_error"] = str(exc)
                scenario_runs[scenario] = scenario_run
                continue

            scenario_run.update(
                {
                    "status": snapshot.status,
                    "error": snapshot.error,
                    "report_url": snapshot.report_url,
                    "last_sync_error": "",
                }
            )
            aggregate_scenarios[scenario] = {
                **snapshot.aggregate,
                "per_model_usage": snapshot.per_model_usage,
                "estimated_cost": _cost_or_unavailable(
                    snapshot.per_model_usage,
                    self._pricing,
                ),
            }
            await self._synchronize_scenario_results(
                comparison_id=comparison_id,
                scenario=scenario,
                cases=cases,
                snapshot=snapshot,
            )
            if snapshot.status in TERMINAL_STATUSES:
                scenario_run["results_synced"] = True
            scenario_runs[scenario] = scenario_run

        aggregate["scenarios"] = aggregate_scenarios
        updated = await self._update_run_with_retry(
            comparison_id,
            {
                "scenario_runs": scenario_runs,
                "status": _comparison_status(scenario_runs),
                "aggregate": aggregate,
                "error": _comparison_error(scenario_runs),
            },
        )
        return EvaluationRun.model_validate(updated)

    async def _load_persisted_cases(
        self,
        run_item: dict[str, Any],
    ) -> list[EvaluationCase]:
        comparison_id = str(run_item["id"])
        documents = await self._results.list(comparison_id)
        by_case_id = {
            str(item.get("case_id")): item
            for item in documents
            if item.get("case_id")
        }
        cases = []
        missing = []
        for case_id in run_item.get("case_ids") or []:
            document = by_case_id.get(str(case_id))
            snapshot = (
                document.get("case_snapshot")
                if document is not None
                else None
            )
            if not isinstance(snapshot, dict):
                missing.append(str(case_id))
                continue
            cases.append(EvaluationCase.model_validate(snapshot))
        if missing:
            raise StorePersistenceError(
                "Evaluation recoverability is incomplete; missing case "
                "snapshots: "
                + ", ".join(missing)
            )
        return cases

    async def _launch_pending_runs(
        self,
        run_item: dict[str, Any],
        cases: list[EvaluationCase],
    ) -> dict[str, Any]:
        comparison_id = str(run_item["id"])
        scenario_runs = {
            key: dict(value)
            for key, value in (run_item.get("scenario_runs") or {}).items()
        }
        for scenario in SCENARIOS:
            scenario_run = scenario_runs.get(scenario) or {}
            if scenario_run.get("id") or scenario_run.get("status") not in {
                "pending_launch",
                "launching",
            }:
                continue
            scenario_run["status"] = "launching"
            scenario_run["error"] = ""
            scenario_runs[scenario] = scenario_run
            run_item = await self._update_run_with_retry(
                comparison_id,
                {
                    "status": "launching",
                    "scenario_runs": scenario_runs,
                },
            )
            try:
                created = await self._foundry.launch_run(
                    prepared=run_item,
                    comparison_id=comparison_id,
                    name=str(run_item.get("name") or ""),
                    scenario=scenario,
                )
            except FoundryOperationError as exc:
                scenario_runs[scenario] = {
                    **scenario_run,
                    "status": "failed",
                    "error": str(exc),
                    "results_synced": True,
                }
                return await self._abort_partial_launch(
                    run_item,
                    cases,
                    scenario_runs,
                    reason=f"{scenario} launch failed: {exc}",
                )

            scenario_runs[scenario] = created
            try:
                run_item = await self._update_run_with_retry(
                    comparison_id,
                    {
                        "status": "launching",
                        "scenario_runs": scenario_runs,
                    },
                )
            except (StoreConflictError, StorePersistenceError) as exc:
                return await self._abort_partial_launch(
                    run_item,
                    cases,
                    scenario_runs,
                    reason=(
                        f"{scenario} external ID persistence failed: {exc}"
                    ),
                )

        return await self._update_run_with_retry(
            comparison_id,
            {
                "scenario_runs": scenario_runs,
                "status": _comparison_status(scenario_runs),
                "error": _comparison_error(scenario_runs),
            },
        )

    async def _abort_partial_launch(
        self,
        run_item: dict[str, Any],
        cases: list[EvaluationCase],
        scenario_runs: dict[str, dict[str, Any]],
        *,
        reason: str,
    ) -> dict[str, Any]:
        evaluation_id = str(run_item["foundry_evaluation_id"])
        for scenario in SCENARIOS:
            scenario_run = scenario_runs.get(scenario) or {}
            run_id = str(scenario_run.get("id") or "")
            if run_id:
                try:
                    canceled = await self._foundry.cancel_run(
                        evaluation_id=evaluation_id,
                        run_id=run_id,
                    )
                except FoundryOperationError as exc:
                    canceled = False
                    cancel_error = str(exc)
                else:
                    cancel_error = ""
                if canceled:
                    scenario_run.update(
                        {
                            "status": "canceled",
                            "error": reason,
                            "results_synced": True,
                        }
                    )
                else:
                    scenario_run.update(
                        {
                            "status": "orphaned",
                            "error": (
                                reason
                                + (
                                    f"; cancellation failed: {cancel_error}"
                                    if cancel_error
                                    else "; SDK cancellation unsupported"
                                )
                            ),
                            "results_synced": True,
                        }
                    )
            elif scenario_run.get("status") in {
                "pending_launch",
                "launching",
            }:
                scenario_run.update(
                    {
                        "status": "failed",
                        "error": reason,
                        "results_synced": True,
                    }
                )
            scenario_runs[scenario] = scenario_run
        updated = await self._update_run_with_retry(
            str(run_item["id"]),
            {
                "scenario_runs": scenario_runs,
                "status": "failed",
                "error": reason,
            },
        )
        for case in cases:
            for scenario in SCENARIOS:
                scenario_run = scenario_runs[scenario]
                await self._update_result_scenario_with_retry(
                    comparison_id=str(run_item["id"]),
                    case_id=case.id,
                    scenario=scenario,
                    scenario_result=None,
                    status=str(scenario_run.get("status") or "failed"),
                    error=str(scenario_run.get("error") or reason),
                )
        return updated

    async def _synchronize_scenario_results(
        self,
        *,
        comparison_id: str,
        scenario: EvaluationScenario,
        cases: list[EvaluationCase],
        snapshot: FoundryRunSnapshot,
    ) -> None:
        rows_by_case = {
            str(row.get("case_id")): row
            for row in snapshot.rows
            if row.get("case_id")
        }
        for case in cases:
            row = rows_by_case.get(case.id)
            scenario_result: dict[str, Any] | None = None
            error = snapshot.error
            if snapshot.status == "completed":
                if row is None:
                    error = "Foundry completed without a result for this case"
                else:
                    scenario_result = _score_row(
                        comparison_id=comparison_id,
                        case=case,
                        scenario=scenario,
                        row=row,
                        pricing=self._pricing,
                    ).model_dump(mode="json")
                    error = scenario_result.get("error", "")
            await self._update_result_scenario_with_retry(
                comparison_id=comparison_id,
                case_id=case.id,
                scenario=scenario,
                scenario_result=scenario_result,
                status=(
                    str(row.get("status") or "completed")
                    if row is not None and snapshot.status == "completed"
                    else snapshot.status
                ),
                error=error,
            )

    async def _update_result_scenario_with_retry(
        self,
        *,
        comparison_id: str,
        case_id: str,
        scenario: EvaluationScenario,
        scenario_result: dict[str, Any] | None,
        status: str,
        error: str,
    ) -> dict[str, Any]:
        for attempt in range(3):
            item = await self._results.get(comparison_id, case_id)
            if item is None:
                raise StoreNotFoundError(
                    f"Evaluation result {comparison_id}/{case_id} not found"
                )
            try:
                return await self._results.update_scenario(
                    comparison_id,
                    case_id,
                    scenario,
                    scenario_result,
                    status=status,
                    error=error,
                    expected_version=int(item.get("version", 1)),
                )
            except StoreConflictError:
                if attempt == 2:
                    raise
        raise AssertionError("result retry loop exited unexpectedly")

    async def _update_run_with_retry(
        self,
        comparison_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        for attempt in range(3):
            item = await self._runs.get(comparison_id)
            if item is None:
                raise StoreNotFoundError(
                    f"Evaluation run {comparison_id} not found"
                )
            try:
                return await self._runs.update(
                    comparison_id,
                    changes,
                    expected_version=int(item.get("version", 1)),
                )
            except StoreConflictError:
                if attempt == 2:
                    raise
        raise AssertionError("run retry loop exited unexpectedly")

    async def list_results(
        self,
        comparison_id: str,
    ) -> list[EvaluationCaseResult]:
        if await self._runs.get(comparison_id) is None:
            raise StoreNotFoundError(
                f"Evaluation run {comparison_id} not found"
            )
        return [
            EvaluationCaseResult.model_validate(item)
            for item in await self._results.list(comparison_id)
        ]

    async def update_human_review(
        self,
        *,
        comparison_id: str,
        case_id: str,
        request: HumanReviewUpdate,
        reviewer_id: str,
        reviewer_name: str,
    ) -> EvaluationCaseResult:
        review = HumanReview(
            agent_framework_score=request.agent_framework_score,
            single_agent_score=request.single_agent_score,
            winner=request.winner,
            comment=request.comment,
            reviewer_id=reviewer_id,
            reviewer_name=reviewer_name,
            reviewed_at=datetime.now(timezone.utc),
        )
        updated = await self._results.update_human_review(
            comparison_id,
            case_id,
            review.model_dump(mode="json"),
            expected_version=request.version,
        )
        return EvaluationCaseResult.model_validate(updated)


def _score_row(
    *,
    comparison_id: str,
    case: EvaluationCase,
    scenario: EvaluationScenario,
    row: dict[str, Any],
    pricing: dict[str, Any],
) -> EvaluationResult:
    raw_output = row.get("raw_output")
    deterministic = score_evaluation_output(case, raw_output, scenario)
    rubric_score = _rubric_score(row.get("evaluator_results") or [])
    evaluator_scores = {
        str(result.get("name") or f"evaluator-{index}"): result.get("score")
        for index, result in enumerate(
            row.get("evaluator_results") or [],
            start=1,
        )
        if isinstance(result, dict)
    }
    token_usage = _row_token_usage(row.get("sample") or {})
    error = ""
    if deterministic.output is None:
        error = "Agent output did not match the canonical evaluation schema"
    return EvaluationResult(
        comparison_id=comparison_id,
        case_id=case.id,
        scenario=scenario,
        output=(
            deterministic.output.model_dump(mode="json", by_alias=True)
            if deterministic.output
            else {}
        ),
        raw_output=raw_output,
        rubric_score=rubric_score,
        deterministic_score=deterministic.score,
        overall_score=composite_score(rubric_score, deterministic.score),
        deterministic_checks=deterministic.checks,
        evaluator_scores=evaluator_scores,
        evaluator_results=[
            result
            for result in row.get("evaluator_results") or []
            if isinstance(result, dict)
        ],
        token_usage=token_usage,
        estimated_cost=_cost_or_unavailable(token_usage, pricing),
        error=error,
    )


def _rubric_score(results: list[Any]) -> float | None:
    for result in results:
        if not isinstance(result, dict):
            continue
        name = str(result.get("name") or "").casefold()
        if "travel_request_quality" not in name and "travel request" not in name:
            continue
        score = result.get("score")
        if not isinstance(score, (int, float)):
            return None
        normalized = float(score) * 100 if 0 <= float(score) <= 1 else float(score)
        return round(max(0, min(100, normalized)), 2)
    return None


def _row_token_usage(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    model = sample.get("model")
    usage = sample.get("usage")
    if not model or not isinstance(usage, dict):
        return {}
    return {str(model): usage}


def _cost_or_unavailable(
    usage: dict[str, Any],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    if not usage:
        return {
            "available": False,
            "currency": pricing.get("currency"),
            "total": None,
            "pricing_updated_at": pricing.get("updated_at"),
            "unavailable_models": [],
            "ignored_model_aliases": [],
            "breakdown": {},
            "reason": "token_usage_unavailable",
        }
    return estimate_token_cost(usage, pricing)


def _result_document(
    comparison_id: str,
    case: EvaluationCase,
    scenario_runs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "comparison_id": comparison_id,
        "case_id": case.id,
        "case_title": case.title,
        "case_input": case.input,
        "case_snapshot": case.model_dump(mode="json"),
        "scenarios": {
            "agent_framework_workflow": None,
            "single_prompt_agent": None,
        },
        "scenario_statuses": {
            scenario: str(
                scenario_runs.get(scenario, {}).get(
                    "status",
                    "pending_launch",
                )
            )
            for scenario in SCENARIOS
        },
        "scenario_errors": {},
        "human_review": None,
    }


def _fail_unlaunched_scenarios(
    scenario_runs: dict[str, dict[str, Any]],
    error: str,
) -> dict[str, dict[str, Any]]:
    return {
        scenario: {
            **dict(scenario_runs.get(scenario) or {}),
            "status": "failed",
            "error": error,
            "results_synced": True,
        }
        for scenario in SCENARIOS
    }


def _comparison_status(scenario_runs: dict[str, dict[str, Any]]) -> str:
    statuses = {
        str((scenario_runs.get(scenario) or {}).get("status") or "unknown")
        for scenario in SCENARIOS
    }
    if statuses == {"completed"}:
        return "completed"
    terminal_statuses = TERMINAL_STATUSES | {"orphaned"}
    if statuses.issubset(terminal_statuses):
        return "partial_failure" if "completed" in statuses else "failed"
    return "running"


def _comparison_error(scenario_runs: dict[str, dict[str, Any]]) -> str:
    errors = [
        f"{scenario}: {run.get('error')}"
        for scenario, run in scenario_runs.items()
        if run.get("error")
    ]
    return "; ".join(errors)
