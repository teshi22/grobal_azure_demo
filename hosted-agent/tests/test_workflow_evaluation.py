import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://example.services.ai.azure.com/api/projects/test",
)

from travel_agent.agents import TravelAgents
from travel_agent.models import EvaluationOutput
from travel_agent.workflow import build_workflow


AGENT_VERSIONS = {
    "travel-request-clarifier": "11",
    "travel-request-planner": "12",
    "travel-request-plan-reviewer": "13",
    "travel-request-policy-narrator": "14",
    "travel-request-approval-writer": "15",
}


class _FakeAgent:
    def __init__(self, *responses: str):
        self._responses = iter(responses)
        self.calls = []

    async def run(self, prompt: str):
        self.calls.append(prompt)
        return SimpleNamespace(text=next(self._responses))


def _agents(
    *,
    clarifier: _FakeAgent,
    planner: _FakeAgent | None = None,
    plan_reviewer: _FakeAgent | None = None,
    policy: _FakeAgent | None = None,
    approval: _FakeAgent | None = None,
) -> TravelAgents:
    return TravelAgents(
        clarifier=clarifier,
        planner=planner or _FakeAgent(),
        plan_reviewer=plan_reviewer or _FakeAgent(),
        policy=policy or _FakeAgent(),
        approval=approval or _FakeAgent(),
        versions=AGENT_VERSIONS,
    )


def _evaluation_envelope(case_id: str, input_text: str) -> str:
    return json.dumps(
        {
            "mode": "evaluation",
            "schema_version": "1",
            "case_id": case_id,
            "input": input_text,
        },
        ensure_ascii=False,
    )


def test_evaluation_incomplete_input_returns_without_request_info():
    async def run():
        clarifier = _FakeAgent(
            json.dumps(
                {
                    "departure": "東京",
                    "destination": "",
                    "schedule": "2026年10月22日",
                    "purpose": "顧客訪問",
                },
                ensure_ascii=False,
            )
        )
        response = await build_workflow(
            _agents(clarifier=clarifier)
        ).as_agent(name="travel-request-workflow").run(
            _evaluation_envelope(
                "case-missing-destination",
                "2026年10月22日に東京から顧客訪問をしたいです。",
            )
        )
        output = EvaluationOutput.model_validate_json(response.text)

        assert output.status == "needs_clarification"
        assert output.request is not None
        assert output.request.destination == ""
        assert output.clarification_questions == [
            "目的地を教えてください。"
        ]
        assert output.itinerary is None
        assert output.diagnostics.case_id == "case-missing-destination"
        assert output.diagnostics.agent_versions == AGENT_VERSIONS
        assert all(
            content.type != "function_call"
            for message in response.messages
            for content in message.contents
        )

    asyncio.run(run())


def test_evaluation_complete_path_stops_at_draft_without_mcp(monkeypatch):
    async def run():
        plan = {
            "departure": "東京",
            "destination": "大阪",
            "purpose": "顧客会議",
            "schedule": "2026-10-20（火）",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "method": "東海道新幹線",
                    "from": "東京",
                    "to": "新大阪",
                    "cost": 14_720,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                    "source_title": "スマートEX",
                },
                {
                    "direction": "復路",
                    "method": "東海道新幹線",
                    "from": "新大阪",
                    "to": "東京",
                    "cost": 14_720,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                    "source_title": "スマートEX",
                },
            ],
            "transportation_cost": 29_440,
            "hotel": None,
            "hotel_cost_per_night": None,
            "hotel_nights": None,
            "total_cost": 29_440,
            "distance_km": 515,
            "travel_time_hours": 2.5,
            "fare_basis": "指定席通常期",
            "searched_at": "",
        }
        clarifier = _FakeAgent(
            json.dumps(
                {
                    "departure": "東京",
                    "destination": "大阪",
                    "schedule": "2026年10月20日",
                    "purpose": "顧客会議",
                },
                ensure_ascii=False,
            )
        )
        planner = _FakeAgent(json.dumps(plan, ensure_ascii=False))
        policy = _FakeAgent("規程に適合しています。")
        approval = _FakeAgent("顧客会議のため大阪へ出張します。")
        prepare = AsyncMock()
        submit = AsyncMock()
        monkeypatch.setattr(
            "travel_agent.executors.prepare_travel_request_submission",
            prepare,
        )
        monkeypatch.setattr(
            "travel_agent.executors.submit_travel_request_with_approval",
            submit,
        )

        response = await build_workflow(
            _agents(
                clarifier=clarifier,
                planner=planner,
                policy=policy,
                approval=approval,
            )
        ).as_agent(name="travel-request-workflow").run(
            _evaluation_envelope(
                "case-standard-tokyo-osaka",
                "2026年10月20日に東京から大阪へ顧客会議に行きます。",
            )
        )
        output = EvaluationOutput.model_validate_json(response.text)

        assert output.status == "draft_ready"
        assert output.request is not None
        assert output.request.departure == "東京"
        assert output.itinerary is not None
        assert output.itinerary.transportation_cost == 29_440
        assert output.fare_total == 29_440
        assert output.policy.compliant is True
        assert output.application_draft == "顧客会議のため大阪へ出張します。"
        assert [citation.url for citation in output.citations] == [
            "https://smart-ex.jp/product/plan/service/"
        ]
        assert output.diagnostics.agent_versions == AGENT_VERSIONS
        assert len(clarifier.calls) == 1
        assert len(planner.calls) == 1
        assert len(policy.calls) == 1
        assert len(approval.calls) == 1
        prepare.assert_not_awaited()
        submit.assert_not_awaited()
        assert all(
            content.type != "function_call"
            for message in response.messages
            for content in message.contents
        )

    asyncio.run(run())
