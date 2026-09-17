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
from travel_agent.workflow_agent import ChatOnlyWorkflowAgent


class _FakeAgent:
    def __init__(self, *responses: str):
        self._responses = iter(responses)
        self.calls = []

    async def run(self, prompt: str):
        self.calls.append(prompt)
        return SimpleNamespace(text=next(self._responses))


def _assert_chat_only(response, expected_text: str) -> None:
    assert expected_text in response.text
    assert all(
        content.type != "function_call"
        for message in response.messages
        for content in message.contents
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
            plan_reviewer=_FakeAgent('{"approved":true}'),
            policy=_FakeAgent("規程に適合しています。"),
            approval=_FakeAgent("出張申請書"),
        )
        workflow = build_workflow(agents)
        assert workflow.name == "travel-request-workflow"
        agent = ChatOnlyWorkflowAgent(
            workflow,
            name="travel-request-workflow",
        )
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
        _assert_chat_only(response, "以下の内容で旅程を検索します")

        response = await agent.run("OK")
        _assert_chat_only(response, "この旅程プランでよろしいですか")

        response = await agent.run("OK")
        _assert_chat_only(response, "申請を送信しますか")

        response = await agent.run("キャンセル")
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


def test_chat_only_agent_shows_hitl_and_accepts_plain_reply(
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
            plan_reviewer=_FakeAgent('{"approved":true}'),
            policy=_FakeAgent("規程に適合しています。"),
            approval=_FakeAgent("出張申請書"),
        )
        agent = ChatOnlyWorkflowAgent(
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
        _assert_chat_only(response, "以下の内容で旅程を検索します")
        assert "博多" in response.text

        response = await agent.run("OK")
        _assert_chat_only(response, "この旅程プランでよろしいですか")

        response = await agent.run("OK")
        _assert_chat_only(response, "申請を送信しますか")
        prepare.assert_awaited_once()
        assert prepare.await_args.args[0]["agent_scenario"] == (
            "agent_framework_workflow"
        )
        assert "conversation_id" not in prepare.await_args.args[0]

    asyncio.run(run())


def test_plan_reviewer_routes_changes_to_planner_and_approval_to_policy(
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
                    "fare_type": "自由席",
                    "source_url": "https://www.navitime.co.jp/transfer/",
                }
            ],
            "transportation_cost": 15_000,
            "hotel": None,
            "hotel_cost_per_night": None,
            "hotel_nights": None,
            "total_cost": 15_000,
            "distance_km": 600,
            "travel_time_hours": 2.5,
        }
        revised_plan = {
            **plan,
            "transportation_legs": [
                {
                    **plan["transportation_legs"][0],
                    "cost": 16_000,
                    "fare_type": "指定席",
                }
            ],
            "transportation_cost": 16_000,
            "total_cost": 16_000,
        }
        planner = _FakeAgent(
            json.dumps(plan, ensure_ascii=False),
            json.dumps(revised_plan, ensure_ascii=False),
        )
        reviewer = _FakeAgent(
            json.dumps(
                {"approved": False},
                ensure_ascii=False,
            ),
            json.dumps(
                {"approved": True},
                ensure_ascii=False,
            ),
        )
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
            planner=planner,
            plan_reviewer=reviewer,
            policy=_FakeAgent("規程に適合しています。"),
            approval=_FakeAgent("出張申請書"),
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
        agent = ChatOnlyWorkflowAgent(
            build_workflow(agents),
            name="travel-request-workflow",
        )

        response = await agent.run("10/5に博多出張。チームミーティング")
        _assert_chat_only(response, "以下の内容で旅程を検索します")
        response = await agent.run("OK")
        _assert_chat_only(response, "この旅程プランでよろしいですか")

        response = await agent.run("指定席に変更してください")
        _assert_chat_only(response, "この旅程プランでよろしいですか")
        assert "指定席" in response.text
        assert len(planner.calls) == 2
        assert "変更要望: 指定席に変更してください" in planner.calls[1]
        first_decision_input = json.loads(reviewer.calls[0])
        assert first_decision_input["user_reply"] == "指定席に変更してください"

        response = await agent.run("オッケーです")
        _assert_chat_only(response, "申請を送信しますか")
        second_decision_input = json.loads(reviewer.calls[1])
        assert second_decision_input["user_reply"] == "オッケーです"
        prepare.assert_awaited_once()

    asyncio.run(run())


def test_chat_only_agent_surfaces_reconfirmation_after_revision():
    async def run():
        agents = TravelAgents(
            clarifier=_FakeAgent(
                json.dumps(
                    {
                        "departure": "大阪",
                        "destination": "博多",
                        "schedule": "2026-10-05",
                        "purpose": "出張",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "departure": "",
                        "destination": "",
                        "schedule": "",
                        "purpose": "チームミーティング",
                    },
                    ensure_ascii=False,
                ),
            ),
            planner=_FakeAgent("{}"),
            plan_reviewer=_FakeAgent(),
            policy=_FakeAgent("規程に適合しています。"),
            approval=_FakeAgent("出張申請書"),
        )
        agent = ChatOnlyWorkflowAgent(
            build_workflow(agents),
            name="travel-request-workflow",
        )

        response = await agent.run("10/5に博多出張")
        _assert_chat_only(response, "以下の内容で旅程を検索します")

        response = await agent.run("目的はチームミーティング")
        _assert_chat_only(response, "以下の内容で旅程を検索します")
        assert "チームミーティング" in response.text

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
            plan_reviewer=_FakeAgent('{"approved":true}'),
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
        agent = ChatOnlyWorkflowAgent(
            build_workflow(agents),
            name="travel-request-workflow",
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
        _assert_chat_only(response, "以下の内容で旅程を検索します")

        response = await agent.run("OK")
        _assert_chat_only(response, "この旅程プランでよろしいですか")

        response = await agent.run("OK")

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
