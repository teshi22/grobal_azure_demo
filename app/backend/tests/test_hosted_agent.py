import asyncio
import json
import os

import pytest

os.environ.setdefault(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://example.services.ai.azure.com/api/projects/test",
)

from app.services import hosted_agent
from app.config import settings
from app.services import foundry
from app.services.hosted_agent import (
    _build_function_output,
    _extract_request_info,
)
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


def _direct_response(request_data: dict) -> dict:
    return {
        "output": [
            {
                "type": "function_call",
                "name": "request_info",
                "call_id": "prompt-call-1",
                "arguments": json.dumps(request_data, ensure_ascii=False),
            }
        ]
    }


class _CaptureResponses:
    def __init__(self):
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return object()


@pytest.mark.parametrize(
    ("scenario", "expected_agent_name", "expected_session"),
    [
        (
            "agent_framework_workflow",
            "hosted-agent",
            {"agent_session_id": "conversation-1"},
        ),
        ("single_prompt_agent", "prompt-agent", None),
    ],
)
def test_playground_first_invocation_uses_scenario_envelope(
    monkeypatch,
    scenario,
    expected_agent_name,
    expected_session,
):
    responses = _CaptureResponses()
    monkeypatch.setattr(settings, "hosted_agent_name", "hosted-agent")
    monkeypatch.setattr(settings, "single_prompt_agent_name", "prompt-agent")
    monkeypatch.setattr(
        foundry,
        "get_hosted_responses_client",
        lambda: responses,
    )

    def get_agent_client(agent_name):
        assert agent_name == expected_agent_name
        return responses

    monkeypatch.setattr(
        foundry,
        "get_agent_responses_client",
        get_agent_client,
    )

    foundry.invoke_playground_agent(
        scenario=scenario,
        conversation_id="conversation-1",
        message="大阪へ出張",
    )

    assert json.loads(responses.kwargs["input"]) == {
        "mode": "playground",
        "conversation_id": "conversation-1",
        "input": "大阪へ出張",
    }
    assert responses.kwargs["store"] is True
    assert responses.kwargs["stream"] is False
    assert "extra_headers" not in responses.kwargs
    if expected_session is None:
        assert "extra_body" not in responses.kwargs
    else:
        assert responses.kwargs["extra_body"] == expected_session


def test_submission_invocation_preserves_existing_hosted_agent_contract(
    monkeypatch,
):
    responses = _CaptureResponses()
    monkeypatch.setattr(
        foundry,
        "get_hosted_responses_client",
        lambda: responses,
    )

    foundry.invoke_hosted_agent(
        conversation_id="conversation-1",
        user_id="user-1",
        message="大阪へ出張",
    )

    assert json.loads(responses.kwargs["input"]) == {
        "conversation_id": "conversation-1",
        "message": "大阪へ出張",
    }
    assert responses.kwargs["store"] is True
    assert responses.kwargs["extra_body"] == {
        "agent_session_id": "conversation-1"
    }
    assert responses.kwargs["extra_headers"] == {
        "x-ms-user-identity": "user-1"
    }


def test_single_prompt_resume_omits_session_and_user_identity(monkeypatch):
    responses = _CaptureResponses()
    monkeypatch.setattr(settings, "single_prompt_agent_name", "prompt-agent")
    monkeypatch.setattr(
        foundry,
        "get_agent_responses_client",
        lambda agent_name: responses,
    )

    foundry.invoke_playground_agent(
        scenario="single_prompt_agent",
        conversation_id="conversation-1",
        previous_response_id="response-1",
        function_call_id="prompt-call-1",
        function_output={"approved": True, "feedback": ""},
    )

    assert responses.kwargs["previous_response_id"] == "response-1"
    assert responses.kwargs["input"] == [
        {
            "type": "function_call_output",
            "call_id": "prompt-call-1",
            "output": json.dumps(
                {"approved": True, "feedback": ""},
                ensure_ascii=False,
            ),
        }
    ]
    assert "extra_body" not in responses.kwargs
    assert "extra_headers" not in responses.kwargs


def test_single_prompt_submission_uses_submission_envelope(monkeypatch):
    responses = _CaptureResponses()
    monkeypatch.setattr(settings, "single_prompt_agent_name", "prompt-agent")
    monkeypatch.setattr(
        foundry,
        "get_agent_responses_client",
        lambda agent_name: responses,
    )

    foundry.invoke_single_prompt_agent(
        interaction_mode="submission",
        conversation_id="conversation-1",
        message="大阪へ出張",
    )

    assert json.loads(responses.kwargs["input"]) == {
        "mode": "submission",
        "conversation_id": "conversation-1",
        "input": "大阪へ出張",
    }
    assert "extra_body" not in responses.kwargs
    assert "extra_headers" not in responses.kwargs


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


def test_extracts_direct_single_agent_submission_and_computes_plan_hash():
    plan = {"departure": "大阪", "destination": "東京"}
    result = _extract_request_info(
        _direct_response(
            {
                "type": "submit_confirmation",
                "message": "申請しますか。",
                "data": {
                    "application_text": "申請書",
                    "plan": plan,
                    "policy_result": "規程適合",
                },
            }
        )
    )

    assert result is not None
    pending, event = result
    expected_hash = hosted_agent._plan_hash(plan)
    assert pending["payload"]["data"]["plan_hash"] == expected_hash
    assert event["data"] == {
        "application_text": "申請書",
        "plan": plan,
        "policy_result": "規程適合",
        "plan_hash": expected_hash,
    }


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


@pytest.mark.parametrize(
    ("request_data", "content", "expected_output"),
    [
        (
            {
                "type": "clarification",
                "message": "目的を入力してください。",
                "data": {"missing_fields": ["purpose"]},
            },
            "顧客訪問",
            {"answer": "顧客訪問"},
        ),
        (
            {
                "type": "request_confirmation",
                "message": "内容を確認してください。",
                "data": {"destination": "大阪"},
            },
            "はい",
            {"confirmed": True, "revision": ""},
        ),
        (
            {
                "type": "plan_review",
                "message": "計画を確認してください。",
                "data": {"destination": "大阪"},
            },
            "日程を変更",
            {"approved": False, "feedback": "日程を変更"},
        ),
    ],
)
def test_normalizes_direct_prompt_agent_requests_and_function_outputs(
    request_data,
    content,
    expected_output,
):
    result = _extract_request_info(_direct_response(request_data))
    assert result is not None
    pending, event = result

    assert pending["call_id"] == "prompt-call-1"
    assert pending["request_id"] == "prompt-call-1"
    assert pending["type"] == request_data["type"]
    assert event["type"] == request_data["type"]
    assert event["message"] == request_data["message"]
    for key, value in request_data["data"].items():
        assert event["data"][key] == value

    output = asyncio.run(
        _build_function_output(
            content=content,
            pending=pending,
            user_id="user-1",
            conversation_id="conversation-1",
        )
    )
    assert output == expected_output


def test_rejects_submission_confirmation_in_playground():
    with pytest.raises(ValueError, match="not supported in playground"):
        _extract_request_info(
            _response(
                {
                    "type": "submit_confirmation",
                    "plan_hash": "abc123",
                }
            ),
            allow_submit_confirmation=False,
        )


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


def test_process_message_upgrades_stored_single_prompt_playground_conversation(
    monkeypatch,
):
    invocations = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "id": conversation_id,
                "user_id": user_id,
                "scenario": "single_prompt_agent",
                "interaction_mode": "playground",
                "foundry_response_id": None,
                "pending_request": None,
            }

        async def update(self, conversation_id, **changes):
            assert conversation_id == "conversation-1"
            assert changes["status"] == "completed"

    class Events:
        async def append(self, *args, **kwargs):
            assert args[1] == "complete"

    class Response:
        output_text = "完了"

        def model_dump(self, **kwargs):
            return {
                "id": "response-1",
                "status": "completed",
                "output_text": self.output_text,
                "output": [],
            }

    def fake_single_prompt(**kwargs):
        invocations.append(kwargs)
        return Response()

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "get_event_store", Events)
    monkeypatch.setattr(
        hosted_agent,
        "invoke_single_prompt_agent",
        fake_single_prompt,
    )
    monkeypatch.setattr(
        hosted_agent,
        "invoke_hosted_agent",
        lambda **kwargs: pytest.fail("submission agent was invoked"),
    )

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="大阪へ出張",
            message_id="message-1",
            idempotency_key="key-1",
        )
    )

    assert invocations == [
        {
            "interaction_mode": "submission",
            "conversation_id": "conversation-1",
            "message": "大阪へ出張",
            "previous_response_id": None,
            "function_call_id": None,
            "function_output": None,
        }
    ]


def test_single_prompt_submission_issues_grant_and_calls_mcp(monkeypatch):
    plan = {
        "departure": "東京",
        "destination": "大阪",
        "schedule": "2026-10-01",
        "purpose": "顧客訪問",
    }
    plan_hash = hosted_agent._plan_hash(plan)
    pending = {
        "call_id": "submit-call-1",
        "type": "submit_confirmation",
        "payload": {
            "data": {
                "application_text": "出張申請書案",
                "plan": plan,
                "policy_result": "規程適合",
                "plan_hash": plan_hash,
            }
        },
    }
    updates = []
    emitted = []
    submissions = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "id": conversation_id,
                "user_id": user_id,
                "scenario": "single_prompt_agent",
                "interaction_mode": "submission",
                "foundry_response_id": "response-1",
                "pending_request": pending,
            }

        async def update(self, conversation_id, **changes):
            updates.append(changes)

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            emitted.append((event_type, data))

    class GrantStore:
        async def issue(self, **kwargs):
            assert kwargs == {
                "user_id": "user-1",
                "conversation_id": "conversation-1",
                "call_id": "submit-call-1",
                "plan_hash": plan_hash,
            }
            return {"id": "grant-1", "idempotency_key": "mcp-key-1"}

    async def fake_submit(arguments):
        submissions.append(arguments)
        return {"success": True, "request_id": "TR-ABC123"}

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "get_event_store", Events)
    monkeypatch.setattr(hosted_agent, "get_approval_grant_store", GrantStore)
    monkeypatch.setattr(hosted_agent, "submit_travel_request", fake_submit)
    monkeypatch.setattr(
        hosted_agent,
        "_invoke_for_conversation",
        lambda **kwargs: pytest.fail("Foundry should not be resumed after submission"),
    )

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="はい",
            message_id="message-1",
            idempotency_key="key-1",
        )
    )

    assert submissions == [
        {
            "conversation_id": "conversation-1",
            "approval_grant_id": "grant-1",
            "idempotency_key": "mcp-key-1",
            "plan_hash": plan_hash,
            "application_text": "出張申請書案",
            "application_data": plan,
        }
    ]
    assert updates[-1]["status"] == "completed"
    assert emitted == [
        (
            "complete",
            {
                "output": (
                    "出張申請書案\n\n"
                    "✅ 出張申請書を送信しました。（申請番号: TR-ABC123）"
                ),
                "request_id": "TR-ABC123",
                "plan": plan,
                "policy_display": "規程適合",
            },
        )
    ]


def test_single_prompt_submission_can_be_cancelled_without_mcp(monkeypatch):
    pending = {
        "call_id": "submit-call-1",
        "type": "submit_confirmation",
        "payload": {
            "data": {
                "application_text": "出張申請書案",
                "plan": {"departure": "東京"},
                "policy_result": "規程適合",
                "plan_hash": "hash-1",
            }
        },
    }
    emitted = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "scenario": "single_prompt_agent",
                "interaction_mode": "submission",
                "pending_request": pending,
            }

        async def update(self, conversation_id, **changes):
            assert changes["status"] == "completed"
            assert changes["pending_request"] is None

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            emitted.append((event_type, data))

    async def fail_submit(arguments):
        pytest.fail("MCP should not be called after cancellation")

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "get_event_store", Events)
    monkeypatch.setattr(hosted_agent, "submit_travel_request", fail_submit)

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="キャンセル",
            message_id="message-1",
            idempotency_key="key-1",
        )
    )

    assert emitted == [
        ("complete", {"output": "出張申請の送信をキャンセルしました。"})
    ]


def test_process_message_defaults_legacy_document_to_submission(monkeypatch):
    invocations = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "id": conversation_id,
                "user_id": user_id,
                "foundry_response_id": None,
                "pending_request": None,
            }

        async def update(self, conversation_id, **changes):
            assert changes["status"] == "completed"

    class Events:
        async def append(self, *args, **kwargs):
            pass

    class Response:
        output_text = "完了"

        def model_dump(self, **kwargs):
            return {
                "id": "response-1",
                "status": "completed",
                "output_text": self.output_text,
                "output": [],
            }

    def fake_hosted(**kwargs):
        invocations.append(kwargs)
        return Response()

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "get_event_store", Events)
    monkeypatch.setattr(hosted_agent, "invoke_hosted_agent", fake_hosted)
    monkeypatch.setattr(
        hosted_agent,
        "invoke_playground_agent",
        lambda **kwargs: pytest.fail("playground agent was invoked"),
    )

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="大阪へ出張",
            message_id="message-1",
            idempotency_key="key-1",
        )
    )

    assert invocations[0]["conversation_id"] == "conversation-1"
    assert invocations[0]["user_id"] == "user-1"
    assert invocations[0]["message"] == "大阪へ出張"


def test_legacy_playground_route_is_normalized_to_submission():
    assert hosted_agent._conversation_route(
        {
            "scenario": "single_prompt_agent",
            "interaction_mode": "playground",
        }
    ) == ("single_prompt_agent", "submission")


def test_skips_message_claimed_by_another_replica(monkeypatch):
    class Store:
        async def claim_pending_message(self, conversation_id):
            assert conversation_id == "conversation-1"
            return None

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)

    asyncio.run(hosted_agent.process_queued_message("conversation-1"))
