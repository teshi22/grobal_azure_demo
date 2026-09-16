import asyncio
import copy

import pytest

from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationRunCreate,
    HumanReviewUpdate,
)
from app.services.cosmos import StoreConflictError, StorePersistenceError
from app.services.evaluation_foundry import FoundryRunSnapshot
from app.services.evaluation_runs import EvaluationRunCoordinator


def _case():
    return EvaluationCase.model_validate(
        {
            "id": "case-001",
            "dataset_id": "travel-request-v1",
            "title": "Tokyo to Osaka",
            "input": "Tokyo to Osaka on 2026-10-20 for a customer meeting",
            "expected_status": "draft_ready",
            "expected_request": {
                "departure": "Tokyo",
                "destination": "Osaka",
                "schedule": "2026-10-20",
                "purpose": "customer meeting",
            },
            "expected_policy_compliant": True,
            "version": 1,
        }
    )


def _output(scenario):
    return {
        "status": "draft_ready",
        "request": _case().expected_request.model_dump(mode="json"),
        "clarification_questions": [],
        "itinerary": {
            "departure": "Tokyo",
            "destination": "Osaka",
            "purpose": "customer meeting",
            "schedule": "2026-10-20",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "round trip",
                    "method": "rail",
                    "from": "Tokyo",
                    "to": "Osaka",
                    "cost": 10000,
                    "fare_type": "reserved",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                    "source_title": "Smart EX",
                }
            ],
            "transportation_cost": 10000,
            "total_cost": 10000,
        },
        "fare_total": 10000,
        "policy": {
            "compliant": True,
            "details": [],
            "narrative": "Compliant",
        },
        "application_draft": "Travel request draft",
        "citations": [],
        "diagnostics": {
            "schema_version": "1",
            "scenario": scenario,
            "case_id": "case-001",
            "agent_versions": {"agent": "1"},
        },
    }


class _CaseStore:
    def __init__(self, cases):
        self.items = {
            (case.dataset_id, case.id): case.model_dump(mode="json")
            for case in cases
        }

    async def list(self, dataset_id, *, enabled=None):
        return [
            copy.deepcopy(item)
            for (dataset, _), item in self.items.items()
            if dataset == dataset_id
            and (enabled is None or item["enabled"] == enabled)
        ]

    async def get(self, case_id, dataset_id):
        return copy.deepcopy(self.items.get((dataset_id, case_id)))


class _RunStore:
    def __init__(self, events=None, fail_external_id_updates=0):
        self.items = {}
        self.events = events
        self.fail_external_id_updates = fail_external_id_updates

    async def create(self, run, creator_id):
        if self.events is not None:
            self.events.append("run-persisted")
        saved = {
            **copy.deepcopy(run),
            "version": 1,
            "created_by": creator_id,
        }
        self.items[run["id"]] = saved
        return copy.deepcopy(saved)

    async def get(self, comparison_id):
        return copy.deepcopy(self.items.get(comparison_id))

    async def update(self, comparison_id, changes, *, expected_version):
        item = self.items[comparison_id]
        persisted_scenarios = [
            scenario
            for scenario, scenario_run in (
                changes.get("scenario_runs") or {}
            ).items()
            if scenario_run.get("id")
            and scenario_run.get("id")
            != (item.get("scenario_runs") or {}).get(scenario, {}).get("id")
        ]
        if (
            self.fail_external_id_updates
            and persisted_scenarios
        ):
            self.fail_external_id_updates -= 1
            raise StorePersistenceError("external ID write failed")
        if item["version"] != expected_version:
            raise StoreConflictError("stale")
        item.update(copy.deepcopy(changes))
        item["version"] += 1
        if self.events is not None:
            for scenario in persisted_scenarios:
                self.events.append(f"id-persisted:{scenario}")
        return copy.deepcopy(item)


class _ResultStore:
    def __init__(self, events=None, fail_create_many=False):
        self.items = {}
        self.events = events
        self.fail_create_many = fail_create_many

    async def create(self, result):
        saved = {
            **copy.deepcopy(result),
            "id": result["case_id"],
            "version": 1,
        }
        self.items[(result["comparison_id"], result["case_id"])] = saved
        return copy.deepcopy(saved)

    async def create_many(self, comparison_id, results):
        if self.events is not None:
            self.events.append("results-persisted")
        if self.fail_create_many:
            raise StorePersistenceError("batch persistence failed")
        saved = []
        for result in results:
            saved.append(await self.create(result))
        return saved

    async def get(self, comparison_id, case_id):
        return copy.deepcopy(self.items.get((comparison_id, case_id)))

    async def list(self, comparison_id):
        return [
            copy.deepcopy(value)
            for (run_id, _), value in self.items.items()
            if run_id == comparison_id
        ]

    async def update_scenario(
        self,
        comparison_id,
        case_id,
        scenario,
        scenario_result,
        *,
        status,
        error="",
        expected_version,
    ):
        item = self.items[(comparison_id, case_id)]
        if item["version"] != expected_version:
            raise StoreConflictError("stale")
        item["scenarios"][scenario] = copy.deepcopy(scenario_result)
        item["scenario_statuses"][scenario] = status
        if error:
            item["scenario_errors"][scenario] = error
        else:
            item["scenario_errors"].pop(scenario, None)
        item["version"] += 1
        return copy.deepcopy(item)

    async def update_human_review(
        self,
        comparison_id,
        case_id,
        review,
        *,
        expected_version,
    ):
        item = self.items[(comparison_id, case_id)]
        if item["version"] != expected_version:
            raise StoreConflictError("stale")
        item["human_review"] = copy.deepcopy(review)
        item["version"] += 1
        return copy.deepcopy(item)


class _FoundryService:
    def __init__(
        self,
        snapshots,
        *,
        events=None,
        fail_launch=None,
        cancel_supported=True,
    ):
        self.snapshots = snapshots
        self.get_calls = []
        self.launch_calls = []
        self.cancel_calls = []
        self.events = events
        self.fail_launch = fail_launch
        self.cancel_supported = cancel_supported

    async def prepare_comparison(self, *, cases):
        return {
            "dataset_id": cases[0].dataset_id,
            "dataset_version": "dataset-v1",
            "foundry_dataset_id": "dataset-id",
            "foundry_evaluation_id": "evaluation-id",
            "evaluation_definition_hash": "definition-hash",
            "scenario_runs": {
                "agent_framework_workflow": {
                    "id": "",
                    "status": "pending_launch",
                    "error": "",
                    "results_synced": False,
                    "agent_name": "hosted",
                    "agent_version": "1",
                },
                "single_prompt_agent": {
                    "id": "",
                    "status": "pending_launch",
                    "error": "",
                    "results_synced": False,
                    "agent_name": "single",
                    "agent_version": "2",
                },
            },
        }

    async def launch_run(
        self,
        *,
        prepared,
        comparison_id,
        name,
        scenario,
    ):
        if self.events is not None:
            self.events.append(f"launch:{scenario}")
        self.launch_calls.append(scenario)
        if scenario == self.fail_launch:
            from app.services.evaluation_foundry import FoundryOperationError

            raise FoundryOperationError(f"{scenario} launch failed")
        return {
            "id": f"run-{scenario}",
            "status": "queued",
            "error": "",
            "results_synced": False,
            "agent_name": prepared["scenario_runs"][scenario]["agent_name"],
            "agent_version": prepared["scenario_runs"][scenario][
                "agent_version"
            ],
        }

    async def cancel_run(self, *, evaluation_id, run_id):
        self.cancel_calls.append(run_id)
        return self.cancel_supported

    async def get_run(self, *, evaluation_id, run_id, cases, scenario):
        self.get_calls.append((run_id, scenario))
        return self.snapshots[scenario]


def _snapshot(scenario, *, status="completed", error=""):
    rows = []
    if status == "completed":
        rows = [
            {
                "case_id": "case-001",
                "raw_output": _output(scenario),
                "evaluator_results": [
                    {
                        "name": "travel_request_quality",
                        "score": (
                            0.9
                            if scenario == "agent_framework_workflow"
                            else 0.8
                        ),
                    }
                ],
                "sample": {
                    "model": "gpt-5.4",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                    },
                },
                "status": "completed",
            }
        ]
    return FoundryRunSnapshot(
        status=status,
        aggregate={"result_counts": {"total": 1}},
        rows=rows,
        per_model_usage={
            "gpt-5.4": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
            }
        },
        error=error,
    )


def _pricing():
    return {
        "currency": "USD",
        "unit": "per_1m_tokens",
        "updated_at": "2026-09-14",
        "models": {"gpt-5.4": {"input": None, "output": None}},
    }


def _coordinator(foundry, run_store=None, result_store=None):
    return EvaluationRunCoordinator(
        case_store=_CaseStore([_case()]),
        run_store=run_store or _RunStore(),
        result_store=result_store or _ResultStore(),
        foundry_service=foundry,
        pricing=_pricing(),
    )


def test_run_sync_scores_both_scenarios_and_is_restart_idempotent():
    snapshots = {
        scenario: _snapshot(scenario)
        for scenario in (
            "agent_framework_workflow",
            "single_prompt_agent",
        )
    }
    run_store = _RunStore()
    result_store = _ResultStore()
    first_foundry = _FoundryService(snapshots)
    coordinator = _coordinator(first_foundry, run_store, result_store)
    asyncio.run(
        coordinator.create_run(
            comparison_id="comparison-1",
            request=EvaluationRunCreate(),
            creator_id="creator",
        )
    )

    restarted_foundry = _FoundryService(snapshots)
    restarted = _coordinator(restarted_foundry, run_store, result_store)
    run = asyncio.run(restarted.sync_run("comparison-1"))
    results = asyncio.run(restarted.list_results("comparison-1"))
    asyncio.run(restarted.sync_run("comparison-1"))

    assert run.status == "completed"
    assert len(restarted_foundry.get_calls) == 2
    assert restarted_foundry.launch_calls == []
    pair = results[0]
    assert pair.scenarios.agent_framework_workflow.deterministic_score == 100
    assert pair.scenarios.agent_framework_workflow.overall_score == 94
    assert pair.scenarios.single_prompt_agent.overall_score == 88
    assert (
        pair.scenarios.agent_framework_workflow.estimated_cost["available"]
        is False
    )
    assert (
        pair.scenarios.agent_framework_workflow.estimated_cost["total"] is None
    )


def test_partial_failure_keeps_paired_case_shape_and_shared_review():
    foundry = _FoundryService(
        {
            "agent_framework_workflow": _snapshot(
                "agent_framework_workflow"
            ),
            "single_prompt_agent": _snapshot(
                "single_prompt_agent",
                status="failed",
                error="agent invocation failed",
            ),
        }
    )
    coordinator = _coordinator(foundry)
    asyncio.run(
        coordinator.create_run(
            comparison_id="comparison-2",
            request=EvaluationRunCreate(),
            creator_id="creator",
        )
    )
    run = asyncio.run(coordinator.sync_run("comparison-2"))
    result = asyncio.run(coordinator.list_results("comparison-2"))[0]
    review_version = result.version
    reviewed = asyncio.run(
        coordinator.update_human_review(
            comparison_id="comparison-2",
            case_id="case-001",
            request=HumanReviewUpdate(
                version=review_version,
                agent_framework_score=5,
                single_agent_score=2,
                winner="agent_framework_workflow",
                comment="Workflow handled the request.",
            ),
            reviewer_id="reviewer-1",
            reviewer_name="Reviewer",
        )
    )

    assert run.status == "partial_failure"
    assert result.scenarios.agent_framework_workflow is not None
    assert result.scenarios.single_prompt_agent is None
    assert (
        result.scenario_errors["single_prompt_agent"]
        == "agent invocation failed"
    )
    assert reviewed.human_review.reviewer_id == "reviewer-1"
    assert reviewed.human_review.reviewer_name == "Reviewer"
    with pytest.raises(StoreConflictError):
        asyncio.run(
            coordinator.update_human_review(
                comparison_id="comparison-2",
                case_id="case-001",
                request=HumanReviewUpdate(
                    version=review_version,
                    agent_framework_score=1,
                    single_agent_score=5,
                    winner="single_prompt_agent",
                ),
                reviewer_id="reviewer-2",
                reviewer_name="Other",
            )
        )


def test_external_runs_start_only_after_run_and_result_persistence():
    events = []
    foundry = _FoundryService(
        {
            scenario: _snapshot(scenario)
            for scenario in (
                "agent_framework_workflow",
                "single_prompt_agent",
            )
        },
        events=events,
    )
    coordinator = EvaluationRunCoordinator(
        case_store=_CaseStore([_case()]),
        run_store=_RunStore(events=events),
        result_store=_ResultStore(events=events),
        foundry_service=foundry,
        pricing=_pricing(),
    )

    asyncio.run(
        coordinator.create_run(
            comparison_id="comparison-order",
            request=EvaluationRunCreate(),
            creator_id="creator",
        )
    )

    assert events == [
        "run-persisted",
        "results-persisted",
        "launch:agent_framework_workflow",
        "id-persisted:agent_framework_workflow",
        "launch:single_prompt_agent",
        "id-persisted:single_prompt_agent",
    ]


def test_result_persistence_failure_starts_no_external_runs():
    foundry = _FoundryService({})
    coordinator = EvaluationRunCoordinator(
        case_store=_CaseStore([_case()]),
        run_store=_RunStore(),
        result_store=_ResultStore(fail_create_many=True),
        foundry_service=foundry,
        pricing=_pricing(),
    )

    run = asyncio.run(
        coordinator.create_run(
            comparison_id="comparison-result-failure",
            request=EvaluationRunCreate(),
            creator_id="creator",
        )
    )

    assert foundry.launch_calls == []
    assert run.status == "failed"
    assert run.result_documents_state == "failed"
    assert "Result document persistence failed" in run.error


def test_partial_external_creation_cancels_already_created_run():
    foundry = _FoundryService(
        {},
        fail_launch="single_prompt_agent",
    )
    run_store = _RunStore()
    result_store = _ResultStore()
    coordinator = EvaluationRunCoordinator(
        case_store=_CaseStore([_case()]),
        run_store=run_store,
        result_store=result_store,
        foundry_service=foundry,
        pricing=_pricing(),
    )

    run = asyncio.run(
        coordinator.create_run(
            comparison_id="comparison-partial-launch",
            request=EvaluationRunCreate(),
            creator_id="creator",
        )
    )
    repeated = asyncio.run(
        coordinator.sync_run("comparison-partial-launch")
    )

    assert foundry.launch_calls == [
        "agent_framework_workflow",
        "single_prompt_agent",
    ]
    assert foundry.cancel_calls == ["run-agent_framework_workflow"]
    assert run.status == "failed"
    assert (
        run.scenario_runs["agent_framework_workflow"]["status"]
        == "canceled"
    )
    assert run.scenario_runs["single_prompt_agent"]["status"] == "failed"
    assert repeated.scenario_runs == run.scenario_runs
    assert len(foundry.launch_calls) == 2


def test_external_id_persistence_failure_cancels_without_duplicate_launch():
    foundry = _FoundryService({})
    run_store = _RunStore(fail_external_id_updates=1)
    coordinator = EvaluationRunCoordinator(
        case_store=_CaseStore([_case()]),
        run_store=run_store,
        result_store=_ResultStore(),
        foundry_service=foundry,
        pricing=_pricing(),
    )

    run = asyncio.run(
        coordinator.create_run(
            comparison_id="comparison-id-failure",
            request=EvaluationRunCreate(),
            creator_id="creator",
        )
    )
    asyncio.run(coordinator.sync_run("comparison-id-failure"))

    assert foundry.launch_calls == ["agent_framework_workflow"]
    assert foundry.cancel_calls == ["run-agent_framework_workflow"]
    assert (
        run.scenario_runs["agent_framework_workflow"]["status"]
        == "canceled"
    )
    assert run.scenario_runs["single_prompt_agent"]["status"] == "failed"


def test_unsupported_cancellation_records_orphaned_external_run():
    foundry = _FoundryService(
        {},
        fail_launch="single_prompt_agent",
        cancel_supported=False,
    )
    coordinator = _coordinator(foundry)

    run = asyncio.run(
        coordinator.create_run(
            comparison_id="comparison-orphan",
            request=EvaluationRunCreate(),
            creator_id="creator",
        )
    )

    orphan = run.scenario_runs["agent_framework_workflow"]
    assert orphan["status"] == "orphaned"
    assert "cancellation unsupported" in orphan["error"]
