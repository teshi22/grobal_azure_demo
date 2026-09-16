import hashlib
import json
import asyncio
import copy

from tools import submit_travel_request as submission
from tools.submit_travel_request import (
    _plan_hash,
    _validate_arguments,
    _validate_direct_arguments,
    _validate_prepare_arguments,
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


def test_prompt_agent_prepare_requires_application_data():
    error = _validate_prepare_arguments(
        {
            "application_text": "申請書",
            "policy_result": "規程適合",
        }
    )
    assert error == "application_data must be an object"


def test_prompt_agent_submission_requires_approval_id():
    error = _validate_direct_arguments({"user_confirmed": True})
    assert error == "approval_id is required"


def test_prompt_agent_submission_requires_explicit_user_confirmation():
    error = _validate_direct_arguments(
        {
            "approval_id": "approval-1",
        }
    )
    assert error == "user_confirmed must be true"


def test_prompt_agent_prepare_and_submit_uses_conversation_owner(monkeypatch):
    plan = {"departure": "大阪", "destination": "東京"}
    created = []

    class Conversations:
        async def read_item(self, item, partition_key):
            assert item == partition_key == "conversation-1"
            return {
                "id": "conversation-1",
                "user_id": "user-1",
                "scenario": "single_prompt_agent",
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
                "user_confirmed": True,
            }
        )
    )

    assert prepared["success"] is True
    assert result["success"] is True
    assert created[0]["user_id"] == "user-1"
    assert created[0]["conversation_id"] == "conversation-1"
    assert created[0]["approval_mode"] == "prompt_agent_mcp"
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
                "application_data": {"departure": "大阪"},
                "policy_result": "規程適合",
            }
        )
    )
    result = asyncio.run(
        submission.submit_travel_request_with_approval(
            {
                "approval_id": prepared["approval_id"],
                "user_confirmed": True,
            }
        )
    )

    assert result["success"] is True
    assert created[0]["user_id"] == "foundry-prompt-agent"
    assert created[0]["conversation_id"].startswith("foundry-")
