import asyncio
import json
from pathlib import Path

import pytest

from app.config import settings
from app.schemas.evaluation import EvaluationCase
from app.services.evaluation_foundry import (
    EvaluationFoundryService,
    FoundryOperationError,
    SdkFoundryEvaluationAdapter,
    close_evaluation_service,
    get_evaluation_service,
)


def _case():
    return EvaluationCase.model_validate(
        {
            "id": "case-001",
            "dataset_id": "travel-request-v1",
            "title": "Tokyo to Osaka",
            "input": "Tokyo to Osaka",
            "expected_status": "draft_ready",
            "expected_request": {
                "departure": "Tokyo",
                "destination": "Osaka",
                "schedule": "2026-10-20",
                "purpose": "Meeting",
            },
            "version": 1,
        }
    )


class _Datasets:
    def get(self, name, version):
        return {"id": f"dataset:{name}:{version}"}


class _Evaluators:
    def list_versions(self, name):
        return [
            {
                "name": name,
                "version": "7",
                "tags": {"definition_hash": "definition-hash"},
            }
        ]


class _OutputItems:
    def list(self, run_id, *, eval_id):
        return [
            {
                "datasource_item": {
                    "case_id": "case-001",
                    "sample.output_text": '{"status":"error"}',
                },
                "results": [
                    {
                        "name": "travel_request_quality",
                        "score": 0.75,
                    }
                ],
                "sample": {
                    "model": "gpt-test",
                    "output": [],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
                "status": "completed",
            }
        ]


class _Runs:
    def __init__(self):
        self.output_items = _OutputItems()
        self.created = []
        self.canceled = []

    def list(self, eval_id, limit):
        return []

    def create(self, eval_id, **kwargs):
        self.created.append((eval_id, kwargs))
        return {"id": "run-1", "status": "queued", "error": None}

    def retrieve(self, run_id, *, eval_id):
        return {
            "id": run_id,
            "status": "completed",
            "error": None,
            "report_url": "https://example/report",
            "result_counts": {"total": 1, "passed": 1},
            "per_testing_criteria_results": [],
            "per_model_usage": [
                {
                    "model_name": "gpt-test",
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "invocation_count": 1,
                }
            ],
        }

    def cancel(self, run_id, *, eval_id):
        self.canceled.append((eval_id, run_id))
        return {"id": run_id, "status": "canceled"}


class _Evals:
    def __init__(self):
        self.runs = _Runs()
        self.created = []

    def list(self, limit):
        return []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "eval-1"}


class _OpenAIClient:
    def __init__(self):
        self.evals = _Evals()
        self.closed = False

    def close(self):
        self.closed = True


class _ProjectClient:
    def __init__(self):
        self.datasets = _Datasets()
        self.beta = type("Beta", (), {"evaluators": _Evaluators()})()


def test_sdk_adapter_normalizes_documented_evaluation_shapes():
    openai_client = _OpenAIClient()
    adapter = SdkFoundryEvaluationAdapter(
        project_client=_ProjectClient(),
        openai_client=openai_client,
    )

    dataset_id = adapter.ensure_dataset(
        name="dataset",
        version="v1",
        file_path="unused.jsonl",
    )
    evaluator = adapter.ensure_rubric_evaluator(
        name="rubric",
        definition_hash="definition-hash",
        rubric={"dimensions": []},
    )
    evaluation_id = adapter.ensure_evaluation(
        name="evaluation",
        definition_hash="definition-hash",
        evaluator=evaluator,
    )
    created = adapter.create_run(
        evaluation_id=evaluation_id,
        name="run",
        dataset_id=dataset_id,
        agent_name="agent",
        agent_version="3",
        comparison_id="comparison",
        scenario="agent_framework_workflow",
    )
    snapshot = adapter.get_run(
        evaluation_id=evaluation_id,
        run_id=created["id"],
        cases=[_case()],
        scenario="agent_framework_workflow",
    )

    assert dataset_id == "dataset:dataset:v1"
    assert evaluator == {"name": "rubric", "version": "7"}
    assert snapshot.rows[0]["case_id"] == "case-001"
    assert snapshot.per_model_usage["gpt-test"]["total_tokens"] == 12
    target = openai_client.evals.runs.created[0][1]["data_source"]["target"]
    assert target == {
        "type": "azure_ai_agent",
        "name": "agent",
        "version": "3",
    }
    input_messages = openai_client.evals.runs.created[0][1][
        "data_source"
    ]["input_messages"]
    assert input_messages["template"][0]["content"]["text"] == (
        "{{item.evaluation_envelope}}"
    )
    assert openai_client.evals.runs.created[0][1]["extra_headers"] == {
        "Idempotency-Key": (
            "evaluation-comparison-agent_framework_workflow"
        )
    }
    assert openai_client.evals.created[0]["testing_criteria"] == [
        {
            "type": "azure_ai_evaluator",
            "name": "travel_request_quality",
            "evaluator_name": "rubric",
            "evaluator_version": "7",
            "initialization_parameters": {
                "deployment_name": settings.evaluation_judge_model
            },
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{sample.output_text}}",
            },
        }
    ]


class _RecordingAdapter:
    def __init__(self):
        self.file_existed = False
        self.dataset_rows = []
        self.created = []

    def ensure_dataset(self, *, name, version, file_path):
        self.file_existed = Path(file_path).is_file()
        self.dataset_rows = [
            json.loads(line)
            for line in Path(file_path).read_text(encoding="utf-8").splitlines()
        ]
        return "dataset-id"

    def ensure_rubric_evaluator(self, **kwargs):
        return {"name": kwargs["name"], "version": "1"}

    def ensure_evaluation(self, **kwargs):
        return "evaluation-id"

    def create_run(self, **kwargs):
        self.created.append(kwargs)
        return {
            "id": f"run-{kwargs['scenario']}",
            "status": "queued",
            "error": "",
            "results_synced": False,
        }

    def cancel_run(self, **kwargs):
        return True

    def get_run(self, **kwargs):
        raise AssertionError("not used")

    def close(self):
        return None


def test_service_materializes_serialized_evaluation_envelope(
    monkeypatch,
):
    temp_dir = Path.cwd() / ".test-evaluation-tmp"
    monkeypatch.setattr(settings, "evaluation_temp_dir", str(temp_dir))
    monkeypatch.setattr(settings, "evaluation_mode", "stub")
    adapter = _RecordingAdapter()
    service = EvaluationFoundryService(adapter)

    prepared = asyncio.run(
        service.prepare_comparison(
            cases=[_case()],
        )
    )

    assert adapter.file_existed
    assert adapter.created == []
    assert not temp_dir.exists()
    row = adapter.dataset_rows[0]
    assert row["query"] == _case().input
    assert json.loads(row["ground_truth"])["expected_status"] == "draft_ready"
    assert json.loads(row["evaluation_envelope"]) == {
        "mode": "evaluation",
        "schema_version": "1",
        "case_id": "case-001",
        "input": "Tokyo to Osaka",
    }
    assert prepared["scenario_runs"]["agent_framework_workflow"][
        "status"
    ] == "pending_launch"


def test_retry_after_header_is_honored_by_adapter_helper():
    response = type(
        "Response",
        (),
        {"headers": {"retry-after": "2.5"}},
    )()
    error = type("Error", (Exception,), {"response": response})()

    assert SdkFoundryEvaluationAdapter._retry_after(error) == 2.5


def test_stub_mode_is_rejected_in_production(monkeypatch):
    close_evaluation_service()
    monkeypatch.setattr(settings, "evaluation_mode", "stub")
    monkeypatch.setattr(settings, "app_environment", "production")

    with pytest.raises(RuntimeError, match="not allowed in production"):
        get_evaluation_service()
