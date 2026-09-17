import hashlib
import json
import asyncio
import copy

import pytest
from tools import submit_travel_request as submission
from tools.submit_travel_request import (
    APPLICATION_DATA_SCHEMA,
    _plan_hash,
    _is_explicit_approval,
    _validate_arguments,
    _validate_direct_arguments,
    _validate_prepare_arguments,
)


@pytest.mark.parametrize(
    "reply",
    [
        "お願いします",
        "はい、お願いします。",
        "この内容でお願いします",
        "問題ありません",
        "大丈夫",
    ],
)
def test_mcp_accepts_natural_explicit_approval(reply):
    assert _is_explicit_approval(reply)


def _day_trip_plan() -> dict:
    return {
        "departure": "大阪",
        "destination": "東京",
        "purpose": "顧客訪問",
        "schedule": "2026-10-20",
        "trip_type": "日帰り",
        "transportation_legs": [
            {
                "direction": "往路",
                "method": "鉄道",
                "from": "大阪駅",
                "to": "東京駅",
                "cost": 10000,
                "fare_type": "指定席",
                "source_url": "https://example.com/outbound",
                "source_title": "往路運賃",
            },
            {
                "direction": "復路",
                "method": "鉄道",
                "from": "東京駅",
                "to": "大阪駅",
                "cost": 10000,
                "fare_type": "指定席",
                "source_url": "https://example.com/return",
                "source_title": "復路運賃",
            },
        ],
        "transportation_cost": 20000,
        "hotel": None,
        "hotel_cost_per_night": None,
        "hotel_nights": None,
        "total_cost": 20000,
        "distance_km": 1000,
        "travel_time_hours": 5,
        "fare_basis": "検索結果",
        "searched_at": "2026-09-16",
    }


def test_plan_hash_uses_compact_utf8_json():
    plan = {"departure": "大阪", "destination": "東京"}
    expected = hashlib.sha256(
        json.dumps(
            plan,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert _plan_hash(plan) == expected


def test_submission_requires_grant_and_structured_plan():
    error = _validate_arguments(
        {
            "application_text": "申請書",
            "conversation_id": "conversation-1",
        }
    )
    assert error == "approval_grant_id is required"


def test_prompt_agent_prepare_requires_application_data():
    error = _validate_prepare_arguments(
        {
            "application_text": "申請書",
            "policy_result": "規程適合",
        }
    )
    assert error == "application_data must be an object"


def test_prepare_tool_exposes_strict_application_schema():
    assert APPLICATION_DATA_SCHEMA["additionalProperties"] is False
    assert APPLICATION_DATA_SCHEMA["properties"]["trip_type"]["enum"] == [
        "日帰り",
        "宿泊",
    ]
    assert APPLICATION_DATA_SCHEMA["properties"]["distance_km"]["type"] == "number"
    assert (
        APPLICATION_DATA_SCHEMA["properties"]["transportation_legs"]["items"][
            "additionalProperties"
        ]
        is False
    )


def test_prompt_agent_prepare_rejects_inconsistent_costs():
    plan = _day_trip_plan()
    plan["total_cost"] = 1

    error = _validate_prepare_arguments(
        {
            "application_text": "申請書",
            "application_data": plan,
            "policy_result": "規程適合",
        }
    )

    assert error == "application_data.total_cost does not match travel costs"


def test_prompt_agent_prepare_rejects_invalid_day_trip_hotel():
    plan = _day_trip_plan()
    plan["hotel"] = "架空ホテル"

    error = _validate_prepare_arguments(
        {
            "application_text": "申請書",
            "application_data": plan,
            "policy_result": "規程適合",
        }
    )

    assert error == "Day trips must not contain hotel details"


def test_prompt_agent_submission_requires_approval_id():
    error = _validate_direct_arguments({"confirmation_text": "申請する"})
    assert error == "approval_id is required"


def test_prompt_agent_submission_requires_explicit_user_confirmation():
    error = _validate_direct_arguments(
        {
            "approval_id": "approval-1",
        }
    )
    assert error == "confirmation_text is required"


@pytest.mark.parametrize(
    ("scenario", "expected_mode"),
    [
        ("single_prompt_agent", "prompt_agent_mcp"),
        ("agent_framework_workflow", "hosted_agent_mcp"),
    ],
)
def test_prepare_and_submit_uses_conversation_owner(
    monkeypatch,
    scenario,
    expected_mode,
):
    plan = _day_trip_plan()
    created = []

    class Conversations:
        async def read_item(self, item, partition_key):
            assert item == partition_key == "conversation-1"
            return {
                "id": "conversation-1",
                "user_id": "user-1",
                "scenario": scenario,
            }

    class Grants:
        def __init__(self):
            self.item = None

        async def create_item(self, item, if_none_match):
            self.item = copy.deepcopy(item)

        async def read_item(self, item, partition_key):
            assert self.item is not None
            assert item == partition_key == self.item["id"]
            return copy.deepcopy(self.item)

        async def replace_item(
            self,
            item,
            body,
            etag,
            match_condition,
        ):
            self.item = copy.deepcopy(body)

    class Requests:
        async def read_item(self, item, partition_key):
            raise submission.CosmosResourceNotFoundError()

        async def create_item(self, document, if_none_match):
            created.append(document)

    grants = Grants()
    monkeypatch.setattr(
        submission,
        "get_conversation_container",
        Conversations,
    )
    monkeypatch.setattr(
        submission,
        "get_approval_grant_container",
        lambda: grants,
    )
    monkeypatch.setattr(submission, "get_container", Requests)

    prepared = asyncio.run(
        submission.prepare_travel_request_submission(
            {
                "application_text": "申請書",
                "conversation_id": "conversation-1",
                "application_data": plan,
                "policy_result": "規程適合",
            }
        )
    )
    result = asyncio.run(
        submission.submit_travel_request_with_approval(
            {
                "approval_id": prepared["approval_id"],
                "confirmation_text": "申請する",
            }
        )
    )

    assert prepared["success"] is True
    assert result["success"] is True
    assert result["submitted"] is True
    assert created[0]["user_id"] == "user-1"
    assert created[0]["conversation_id"] == "conversation-1"
    assert created[0]["approval_mode"] == expected_mode
    assert created[0]["departure"] == "大阪"
    assert grants.item["status"] == "consumed"


def test_prompt_agent_prepare_without_app_context_is_standalone(monkeypatch):
    created = []

    class Grants:
        def __init__(self):
            self.item = None

        async def create_item(self, item, if_none_match):
            self.item = copy.deepcopy(item)

        async def read_item(self, item, partition_key):
            return copy.deepcopy(self.item)

        async def replace_item(
            self,
            item,
            body,
            etag,
            match_condition,
        ):
            self.item = copy.deepcopy(body)

    class Requests:
        async def read_item(self, item, partition_key):
            raise submission.CosmosResourceNotFoundError()

        async def create_item(self, document, if_none_match):
            created.append(document)

    grants = Grants()
    monkeypatch.setattr(
        submission,
        "get_approval_grant_container",
        lambda: grants,
    )
    monkeypatch.setattr(submission, "get_container", Requests)

    prepared = asyncio.run(
        submission.prepare_travel_request_submission(
            {
                "application_text": "申請書",
                "application_data": _day_trip_plan(),
                "policy_result": "規程適合",
            }
        )
    )
    result = asyncio.run(
        submission.submit_travel_request_with_approval(
            {
                "approval_id": prepared["approval_id"],
                "confirmation_text": "申請する",
            }
        )
    )

    assert result["success"] is True
    assert created[0]["user_id"] == "foundry-prompt-agent"
    assert created[0]["conversation_id"].startswith("foundry-")


def test_hosted_agent_prepare_without_app_context_uses_hosted_identity(
    monkeypatch,
):
    created = []

    class Grants:
        def __init__(self):
            self.item = None

        async def create_item(self, item, if_none_match):
            self.item = copy.deepcopy(item)

        async def read_item(self, item, partition_key):
            return copy.deepcopy(self.item)

        async def replace_item(
            self,
            item,
            body,
            etag,
            match_condition,
        ):
            self.item = copy.deepcopy(body)

    class Requests:
        async def read_item(self, item, partition_key):
            raise submission.CosmosResourceNotFoundError()

        async def create_item(self, document, if_none_match):
            created.append(document)

    grants = Grants()
    monkeypatch.setattr(
        submission,
        "get_approval_grant_container",
        lambda: grants,
    )
    monkeypatch.setattr(submission, "get_container", Requests)

    prepared = asyncio.run(
        submission.prepare_travel_request_submission(
            {
                "application_text": "申請書",
                "agent_scenario": "agent_framework_workflow",
                "application_data": _day_trip_plan(),
                "policy_result": "規程適合",
            }
        )
    )
    result = asyncio.run(
        submission.submit_travel_request_with_approval(
            {
                "approval_id": prepared["approval_id"],
                "confirmation_text": "申請する",
            }
        )
    )

    assert result["success"] is True
    assert created[0]["user_id"] == "foundry-hosted-agent"
    assert created[0]["approval_mode"] == "hosted_agent_mcp"


def test_mcp_cancels_fixed_application_from_raw_user_response(monkeypatch):
    class Grants:
        def __init__(self):
            self.item = {
                "id": "approval-1",
                "status": "awaiting_confirmation",
                "expires_at": "2999-01-01T00:00:00+00:00",
                "_etag": "etag-1",
            }

        async def read_item(self, item, partition_key):
            assert item == partition_key == "approval-1"
            return copy.deepcopy(self.item)

        async def replace_item(self, item, body, etag, match_condition):
            assert item == "approval-1"
            assert etag == "etag-1"
            self.item = copy.deepcopy(body)

    grants = Grants()
    monkeypatch.setattr(
        submission,
        "get_approval_grant_container",
        lambda: grants,
    )

    result = asyncio.run(
        submission.submit_travel_request_with_approval(
            {
                "approval_id": "approval-1",
                "confirmation_text": "キャンセル",
            }
        )
    )

    assert result == {
        "success": True,
        "submitted": False,
        "cancelled": True,
        "duplicate": False,
        "request_id": "",
        "submitted_at": "",
        "message": "出張申請の送信をキャンセルしました。",
    }
    assert grants.item["status"] == "cancelled"
    assert grants.item["confirmation_text"] == "キャンセル"
