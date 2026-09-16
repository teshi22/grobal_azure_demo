import hashlib
import json
import asyncio

from tools import submit_travel_request as submission
from tools.submit_travel_request import (
    _plan_hash,
    _validate_arguments,
    _validate_direct_arguments,
)


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


def test_direct_submission_requires_conversation_capability():
    error = _validate_direct_arguments(
        {
            "application_text": "申請書",
            "conversation_id": "conversation-1",
            "application_data": {},
            "policy_result": "規程適合",
        }
    )
    assert error == "submission_token is required"


def test_direct_submission_requires_explicit_user_confirmation():
    error = _validate_direct_arguments(
        {
            "application_text": "申請書",
            "conversation_id": "conversation-1",
            "submission_token": "token-1",
            "application_data": {},
            "policy_result": "規程適合",
        }
    )
    assert error == "user_confirmed must be true"


def test_direct_submission_uses_conversation_owner(monkeypatch):
    plan = {"departure": "大阪", "destination": "東京"}
    created = []

    class Conversations:
        async def read_item(self, item, partition_key):
            assert item == partition_key == "conversation-1"
            return {
                "id": "conversation-1",
                "user_id": "user-1",
                "scenario": "single_prompt_agent",
                "submission_token": "token-1",
            }

    class Requests:
        async def read_item(self, item, partition_key):
            raise submission.CosmosResourceNotFoundError()

        async def create_item(self, document, if_none_match):
            created.append(document)

    monkeypatch.setattr(
        submission,
        "get_conversation_container",
        Conversations,
    )
    monkeypatch.setattr(submission, "get_container", Requests)

    result = asyncio.run(
        submission.submit_travel_request_with_approval(
            {
                "application_text": "申請書",
                "conversation_id": "conversation-1",
                "submission_token": "token-1",
                "user_confirmed": True,
                "application_data": plan,
                "policy_result": "規程適合",
            }
        )
    )

    assert result["success"] is True
    assert created[0]["user_id"] == "user-1"
    assert created[0]["approval_mode"] == "prompt_agent_mcp"
    assert created[0]["departure"] == "大阪"
