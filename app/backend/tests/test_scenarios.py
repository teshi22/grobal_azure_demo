import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://example.services.ai.azure.com/api/projects/test",
)

from app.config import settings
from app.main import app
from app.services import foundry, scenario_runs
from app.services.scenario_runs import ScenarioExecutionError


def _agent_output(
    *,
    scenario: str,
    run_id: str,
) -> dict:
    return {
        "status": "draft_ready",
        "request": {
            "departure": "東京",
            "destination": "大阪",
            "schedule": "2026-10-01",
            "purpose": "顧客訪問",
        },
        "clarification_questions": [],
        "itinerary": {
            "departure": "東京",
            "destination": "大阪",
            "purpose": "顧客訪問",
            "schedule": "2026-10-01",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "method": "新幹線",
                    "from": "東京駅",
                    "to": "新大阪駅",
                    "cost": 13870,
                    "fare_type": "指定席",
                    "source_url": "https://railway.jr-central.co.jp/",
                    "source_title": "JR東海",
                }
            ],
            "transportation_cost": 13870,
            "total_cost": 13870,
        },
        "fare_total": 13870,
        "policy": {
            "compliant": True,
            "details": ["規程内です。"],
            "narrative": "申請可能です。",
        },
        "application_draft": "東京から大阪への出張を申請します。",
        "citations": [],
        "diagnostics": {
            "schema_version": "1",
            "scenario": scenario,
            "case_id": run_id,
            "agent_versions": {"agent": "1"},
        },
    }


class _FakeResponse:
    def __init__(self, output: dict) -> None:
        self.output_text = json.dumps(output, ensure_ascii=False)

    def model_dump(self, **_kwargs):
        return {
            "status": "completed",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 80,
                "total_tokens": 200,
            },
        }


class _CaptureResponses:
    def __init__(self) -> None:
        self.kwargs = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return object()


def test_foundry_invocation_adds_session_only_for_hosted_agents(monkeypatch):
    responses = _CaptureResponses()
    monkeypatch.setattr(
        foundry,
        "get_agent_responses_client",
        lambda _agent_name: responses,
    )

    foundry.invoke_scenario_agent(
        agent_name="hosted-agent",
        session_id="run-1",
        envelope={"input": "request"},
    )
    assert responses.kwargs["extra_body"] == {
        "agent_session_id": "run-1"
    }
    assert responses.kwargs["store"] is False

    foundry.invoke_scenario_agent(
        agent_name="prompt-agent",
        session_id=None,
        envelope={"input": "request"},
    )
    assert "extra_body" not in responses.kwargs


def test_local_stub_api_runs_each_scenario_without_evaluation(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_mode", "stub")
    monkeypatch.setattr(settings, "app_environment", "development")
    monkeypatch.setattr(settings, "entra_tenant_id", "")
    monkeypatch.setattr(settings, "entra_client_id", "")

    with TestClient(app) as client:
        workflow_response = client.post(
            "/api/scenarios/run",
            json={
                "scenario": "agent_framework_workflow",
                "input": "東京から大阪へ顧客訪問",
            },
        )
        single_response = client.post(
            "/api/scenarios/run",
            json={
                "scenario": "single_prompt_agent",
                "input": "東京から大阪へ顧客訪問",
            },
        )

    assert workflow_response.status_code == 200
    assert single_response.status_code == 200
    assert workflow_response.json()["execution_mode"] == "stub"
    assert single_response.json()["execution_mode"] == "stub"
    assert (
        workflow_response.json()["output"]["diagnostics"]["scenario"]
        == "agent_framework_workflow"
    )
    assert (
        single_response.json()["output"]["diagnostics"]["scenario"]
        == "single_prompt_agent"
    )


def test_scenario_api_rejects_blank_input(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_mode", "stub")
    monkeypatch.setattr(settings, "app_environment", "development")
    monkeypatch.setattr(settings, "entra_tenant_id", "")
    monkeypatch.setattr(settings, "entra_client_id", "")

    with TestClient(app) as client:
        response = client.post(
            "/api/scenarios/run",
            json={
                "scenario": "agent_framework_workflow",
                "input": "   ",
            },
        )

    assert response.status_code == 422


def test_foundry_scenario_run_validates_and_returns_usage(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_mode", "foundry")
    monkeypatch.setattr(settings, "hosted_agent_version", "17")

    def fake_invoke(**kwargs):
        envelope = kwargs["envelope"]
        assert kwargs["agent_name"] == settings.hosted_agent_name
        assert kwargs["session_id"] == envelope["case_id"]
        assert envelope["mode"] == "evaluation"
        return _FakeResponse(
            _agent_output(
                scenario="agent_framework_workflow",
                run_id=envelope["case_id"],
            )
        )

    monkeypatch.setattr(scenario_runs, "invoke_scenario_agent", fake_invoke)

    result = asyncio.run(
        scenario_runs.run_scenario(
            scenario="agent_framework_workflow",
            request_input="東京から大阪へ顧客訪問",
        )
    )

    assert result.execution_mode == "foundry"
    assert result.agent_version == "17"
    assert result.token_usage["total_tokens"] == 200
    assert result.output.status == "draft_ready"


def test_single_agent_run_omits_hosted_agent_session_id(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_mode", "foundry")

    def fake_invoke(**kwargs):
        envelope = kwargs["envelope"]
        assert kwargs["session_id"] is None
        return _FakeResponse(
            _agent_output(
                scenario="single_prompt_agent",
                run_id=envelope["case_id"],
            )
        )

    monkeypatch.setattr(scenario_runs, "invoke_scenario_agent", fake_invoke)

    result = asyncio.run(
        scenario_runs.run_scenario(
            scenario="single_prompt_agent",
            request_input="東京から大阪へ顧客訪問",
        )
    )

    assert result.scenario == "single_prompt_agent"


def test_foundry_scenario_run_rejects_mismatched_diagnostics(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_mode", "foundry")

    def fake_invoke(**kwargs):
        return _FakeResponse(
            _agent_output(
                scenario="single_prompt_agent",
                run_id=kwargs["envelope"]["case_id"],
            )
        )

    monkeypatch.setattr(scenario_runs, "invoke_scenario_agent", fake_invoke)

    with pytest.raises(ScenarioExecutionError, match="scenario does not match"):
        asyncio.run(
            scenario_runs.run_scenario(
                scenario="agent_framework_workflow",
                request_input="東京から大阪へ顧客訪問",
            )
        )
