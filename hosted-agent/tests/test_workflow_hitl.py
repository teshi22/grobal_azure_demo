import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://example.services.ai.azure.com/api/projects/test",
)

from agent_framework import Content, Message

from travel_agent.agents import TravelAgents
from travel_agent.models import EvaluationOutput
from travel_agent.workflow import build_workflow
from travel_agent.workflow_agent import ChatCompatibleWorkflowAgent


class _FakeAgent:
    def __init__(self, *responses: str):
        self._responses = iter(responses)

    async def run(self, _prompt: str):
        return SimpleNamespace(text=next(self._responses))


def _function_call(response):
    for message in response.messages:
        for content in message.contents:
            if content.type == "function_call":
                return content
    raise AssertionError("Expected a request_info function call")


def _function_result(call_id: str, payload: str) -> Message:
    return Message(
        role="tool",
        contents=[
            Content.from_function_result(
                call_id,
                result=payload,
            )
        ],
    )


def test_workflow_pauses_and_resumes_through_submission_confirmation(
    monkeypatch,
):
    async def run():
        plan = {
            "departure": "大阪",
            "destination": "東京",
            "purpose": "会議",
            "schedule": "2026-09-10（木）",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "method": "新幹線",
                    "from": "大阪駅",
                    "to": "東京駅",
                    "cost": 10_000,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                },
                {
                    "direction": "復路",
                    "method": "新幹線",
                    "from": "東京駅",
                    "to": "大阪駅",
                    "cost": 10_000,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                },
            ],
            "transportation_cost": 20_000,
            "hotel": None,
            "hotel_cost_per_night": None,
            "hotel_nights": None,
            "total_cost": 22_500,
            "distance_km": 500,
            "travel_time_hours": 2.5,
        }
        agents = TravelAgents(
            clarifier=_FakeAgent(
                json.dumps(
                    {
                        "departure": "大阪",
                        "destination": "東京",
                        "schedule": "2026-09-10",
                        "purpose": "会議",
                    },
                    ensure_ascii=False,
                )
            ),
            planner=_FakeAgent(json.dumps(plan, ensure_ascii=False)),
            policy=_FakeAgent("規程に適合しています。"),
            approval=_FakeAgent("出張申請書"),
        )
        workflow = build_workflow(agents)
        assert workflow.name == "travel-request-workflow"
        agent = workflow.as_agent(name="travel-request-workflow")
        prepare = AsyncMock(
            return_value={
                "success": True,
                "approval_id": "approval-1",
            }
        )
        submit = AsyncMock(
            return_value={
                "success": True,
                "submitted": False,
                "cancelled": True,
                "message": "出張申請の送信をキャンセルしました。",
            }
        )
        monkeypatch.setattr(
            "travel_agent.executors.prepare_travel_request_submission",
            prepare,
        )
        monkeypatch.setattr(
            "travel_agent.executors.submit_travel_request_with_approval",
            submit,
        )

        response = await agent.run(
            json.dumps(
                {"conversation_id": "conversation-1", "message": "東京へ会議"},
                ensure_ascii=False,
            )
        )
        request_confirmation = _function_call(response)
        assert (
            request_confirmation.arguments["request_event"]["data"].type
            == "request_confirmation"
        )

        response = await agent.run(
            _function_result(request_confirmation.call_id, "OK")
        )
        plan_review = _function_call(response)
        assert plan_review.arguments["request_event"]["data"].type == "plan_review"

        response = await agent.run(
            _function_result(plan_review.call_id, "OK")
        )
        submit_confirmation = _function_call(response)
        assert (
            submit_confirmation.arguments["request_event"]["data"].type
            == "submit_confirmation"
        )

        response = await agent.run(
            _function_result(submit_confirmation.call_id, "キャンセル")
        )
        assert response.text == "出張申請の送信をキャンセルしました。"
        assert prepare.await_count == 1
        assert prepare.await_args.args[0]["conversation_id"] == "conversation-1"
        submit.assert_awaited_once_with(
            {
                "approval_id": "approval-1",
                "confirmation_text": "キャンセル",
            }
        )

    asyncio.run(run())


def test_chat_compatible_agent_shows_hitl_and_accepts_plain_reply(
    monkeypatch,
):
    async def run():
        plan = {
            "departure": "大阪",
            "destination": "博多",
            "purpose": "チームミーティング",
            "schedule": "2026-10-05（月）",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "method": "新幹線",
                    "from": "新大阪駅",
                    "to": "博多駅",
                    "cost": 15_000,
                    "fare_type": "指定席",
                    "source_url": "https://www.navitime.co.jp/transfer/",
                },
                {
                    "direction": "復路",
                    "method": "新幹線",
                    "from": "博多駅",
                    "to": "新大阪駅",
                    "cost": 15_000,
                    "fare_type": "指定席",
                    "source_url": "https://www.navitime.co.jp/transfer/",
                },
            ],
            "transportation_cost": 30_000,
            "hotel": None,
            "hotel_cost_per_night": None,
            "hotel_nights": None,
            "total_cost": 30_000,
            "distance_km": 1_200,
            "travel_time_hours": 5,
        }
        agents = TravelAgents(
            clarifier=_FakeAgent(
                json.dumps(
                    {
                        "departure": "大阪",
                        "destination": "博多",
                        "schedule": "2026-10-05",
                        "purpose": "チームミーティング",
                    },
                    ensure_ascii=False,
                )
            ),
            planner=_FakeAgent(json.dumps(plan, ensure_ascii=False)),
            policy=_FakeAgent("規程に適合しています。"),
            approval=_FakeAgent("出張申請書"),
        )
        agent = ChatCompatibleWorkflowAgent(
            build_workflow(agents),
            name="travel-request-workflow",
        )
        prepare = AsyncMock(
            return_value={
                "success": True,
                "approval_id": "approval-1",
            }
        )
        monkeypatch.setattr(
            "travel_agent.executors.prepare_travel_request_submission",
            prepare,
        )

        response = await agent.run("10/5に博多出張。チームミーティング")
        request_confirmation = _function_call(response)
        assert "以下の内容で旅程を検索します" in response.text
        assert "博多" in response.text

        response = await agent.run("OK")
        plan_review = _function_call(response)
        assert plan_review.call_id != request_confirmation.call_id

        response = await agent.run("OK")
        submit_confirmation = _function_call(response)
        assert submit_confirmation.call_id != plan_review.call_id
        prepare.assert_awaited_once()
        assert prepare.await_args.args[0]["agent_scenario"] == (
            "agent_framework_workflow"
        )
        assert "conversation_id" not in prepare.await_args.args[0]

    asyncio.run(run())


def test_playground_keeps_hitl_and_stops_before_submission(monkeypatch):
    async def run():
        plan = {
            "departure": "大阪",
            "destination": "東京",
            "purpose": "会議",
            "schedule": "2026-09-10（木）",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "method": "新幹線",
                    "from": "大阪駅",
                    "to": "東京駅",
                    "cost": 10_000,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                }
            ],
            "transportation_cost": 10_000,
            "hotel": None,
            "hotel_cost_per_night": None,
            "hotel_nights": None,
            "total_cost": 10_000,
            "distance_km": 500,
            "travel_time_hours": 2.5,
        }
        agents = TravelAgents(
            clarifier=_FakeAgent(
                json.dumps(
                    {
                        "departure": "大阪",
                        "destination": "東京",
                        "schedule": "2026-09-10",
                        "purpose": "会議",
                    },
                    ensure_ascii=False,
                )
            ),
            planner=_FakeAgent(json.dumps(plan, ensure_ascii=False)),
            policy=_FakeAgent("規程に適合しています。"),
            approval=_FakeAgent("出張申請書"),
        )
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
        agent = build_workflow(agents).as_agent(
            name="travel-request-workflow"
        )

        response = await agent.run(
            json.dumps(
                {
                    "mode": "playground",
                    "conversation_id": "playground-1",
                    "input": "東京へ会議",
                },
                ensure_ascii=False,
            )
        )
        request_confirmation = _function_call(response)
        assert (
            request_confirmation.arguments["request_event"]["data"].type
            == "request_confirmation"
        )

        response = await agent.run(
            _function_result(request_confirmation.call_id, "OK")
        )
        plan_review = _function_call(response)
        assert plan_review.arguments["request_event"]["data"].type == "plan_review"

        response = await agent.run(
            _function_result(plan_review.call_id, "OK")
        )

        output = EvaluationOutput.model_validate_json(response.text)
        assert output.status == "draft_ready"
        assert output.application_draft.startswith("出張申請書")
        assert "出張申請は送信していません" in output.application_draft
        assert output.diagnostics.case_id == "playground-1"
        assert all(
            content.type != "function_call"
            for message in response.messages
            for content in message.contents
        )
        prepare.assert_not_awaited()
        submit.assert_not_awaited()

    asyncio.run(run())
