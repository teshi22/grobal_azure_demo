import asyncio
import json
import os

import pytest

os.environ.setdefault(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://example.services.ai.azure.com/api/projects/test",
)

from app.services import hosted_agent
from app.services.hosted_agent import _extract_request_info
from travel_agent.models import (
    PlanReviewRequest,
    RequestConfirmationRequest,
    SubmissionConfirmationRequest,
)


def _response(request_data: dict) -> dict:
    return {
        "output": [
            {
                "type": "function_call",
                "name": "request_info",
                "call_id": "call-1",
                "arguments": json.dumps(
                    {
                        "request_id": "request-1",
                        "request_event": {
                            "type": "request_info",
                            "data": request_data,
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        ]
    }


def _serialized_response(payload: str) -> dict:
    return _response(json.loads(payload))


def test_extracts_request_confirmation_from_workflow_dataclass_shape():
    result = _extract_request_info(
        _response(
            {
                "type": "request_confirmation",
                "enriched_request": "出発地: 大阪",
                "fields": {
                    "departure": "大阪",
                    "destination": "東京",
                },
            }
        )
    )
    assert result is not None
    pending, event = result
    assert pending["call_id"] == "call-1"
    assert pending["request_id"] == "request-1"
    assert event["type"] == "request_confirmation"
    assert event["data"]["destination"] == "東京"


def test_extracts_submission_payload_for_ui_and_grant():
    plan = {"departure": "大阪", "destination": "東京"}
    result = _extract_request_info(
        _response(
            {
                "type": "submit_confirmation",
                "application_text": "申請書",
                "plan_json": json.dumps(plan, ensure_ascii=False),
                "policy_narrative": "規程適合",
                "plan_hash": "abc123",
            }
        )
    )
    assert result is not None
    pending, event = result
    assert pending["payload"]["data"]["plan_hash"] == "abc123"
    assert event["data"]["plan"] == plan
    assert event["data"]["policy_result"] == "規程適合"


def test_extracts_serialized_request_confirmation_payload():
    result = _extract_request_info(
        _serialized_response(
            RequestConfirmationRequest(
                enriched_request="出発地: 大阪",
                fields={"departure": "大阪", "destination": "東京"},
            ).convert_to_payload()
        )
    )
    assert result is not None
    _, event = result
    assert event["data"]["destination"] == "東京"


def test_extracts_serialized_plan_review_payload():
    result = _extract_request_info(
        _serialized_response(
            PlanReviewRequest(
                plan_json=json.dumps(
                    {"departure": "大阪", "destination": "東京"},
                    ensure_ascii=False,
                )
            ).convert_to_payload()
        )
    )
    assert result is not None
    _, event = result
    assert event["data"]["departure"] == "大阪"


def test_extracts_serialized_submission_payload():
    result = _extract_request_info(
        _serialized_response(
            SubmissionConfirmationRequest(
                application_text="申請書",
                plan_json=json.dumps(
                    {"departure": "大阪", "destination": "東京"},
                    ensure_ascii=False,
                ),
                policy_narrative="規程適合",
                plan_hash="abc123",
            ).convert_to_payload()
        )
    )
    assert result is not None
    pending, event = result
    assert pending["payload"]["data"]["plan_hash"] == "abc123"
    assert event["data"]["application_text"] == "申請書"


def test_raises_for_failed_hosted_agent_response():
    with pytest.raises(RuntimeError, match="literal_error"):
        hosted_agent._raise_for_response_error(
            {
                "status": "failed",
                "error": {
                    "code": "server_error",
                    "message": "trip_type literal_error",
                },
            }
        )


def test_submission_approval_issues_grant_from_normalized_payload(monkeypatch):
    result = _extract_request_info(
        _response(
            {
                "type": "submit_confirmation",
                "application_text": "申請書",
                "plan_json": json.dumps(
                    {"departure": "大阪", "destination": "東京"},
                    ensure_ascii=False,
                ),
                "policy_narrative": "規程適合",
                "plan_hash": "abc123",
            }
        )
    )
    assert result is not None
    pending, _ = result

    class GrantStore:
        async def issue(self, **kwargs):
            assert kwargs["plan_hash"] == "abc123"
            return {"id": "grant-1", "idempotency_key": "key-1"}

    monkeypatch.setattr(
        hosted_agent,
        "get_approval_grant_store",
        GrantStore,
    )
    output = asyncio.run(
        hosted_agent._build_function_output(
            content="はい",
            pending=pending,
            user_id="user-1",
            conversation_id="conversation-1",
        )
    )
    assert output == {
        "approved": True,
        "approval_grant_id": "grant-1",
        "idempotency_key": "key-1",
    }


def test_processes_claimed_durable_message(monkeypatch):
    message = {
        "message_id": "message-1",
        "user_id": "user-1",
        "content": "大阪へ出張",
        "idempotency_key": "key-1",
    }
    processed = []

    class Store:
        async def claim_pending_message(self, conversation_id):
            assert conversation_id == "conversation-1"
            return message

    async def fake_process_message(**kwargs):
        processed.append(kwargs)

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "process_message", fake_process_message)

    asyncio.run(hosted_agent.process_queued_message("conversation-1"))

    assert processed == [{"conversation_id": "conversation-1", **message}]


def test_skips_message_claimed_by_another_replica(monkeypatch):
    class Store:
        async def claim_pending_message(self, conversation_id):
            assert conversation_id == "conversation-1"
            return None

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)

    asyncio.run(hosted_agent.process_queued_message("conversation-1"))
