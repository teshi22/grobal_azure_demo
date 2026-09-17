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


class _CaptureResponses:
    def __init__(self):
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return object()


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


def test_single_prompt_resume_sends_plain_conversation_turn(monkeypatch):
    responses = _CaptureResponses()
    monkeypatch.setattr(settings, "single_prompt_agent_name", "prompt-agent")
    monkeypatch.setattr(
        foundry,
        "get_agent_responses_client",
        lambda agent_name: responses,
    )

    foundry.invoke_single_prompt_agent(
        conversation_id="conversation-1",
        message="はい",
        previous_response_id="response-1",
    )

    assert responses.kwargs["previous_response_id"] == "response-1"
    assert json.loads(responses.kwargs["input"]) == {
        "conversation_id": "conversation-1",
        "input": "はい",
    }
    assert "extra_body" not in responses.kwargs
    assert "extra_headers" not in responses.kwargs


def test_single_prompt_initial_turn_uses_modeless_input(monkeypatch):
    responses = _CaptureResponses()
    monkeypatch.setattr(settings, "single_prompt_agent_name", "prompt-agent")
    monkeypatch.setattr(
        foundry,
        "get_agent_responses_client",
        lambda agent_name: responses,
    )

    foundry.invoke_single_prompt_agent(
        conversation_id="conversation-1",
        message="大阪へ出張",
    )

    assert json.loads(responses.kwargs["input"]) == {
        "conversation_id": "conversation-1",
        "input": "大阪へ出張",
    }
    assert "extra_body" not in responses.kwargs
    assert "extra_headers" not in responses.kwargs


def test_hosted_resume_sends_plain_conversation_turn(monkeypatch):
    responses = _CaptureResponses()
    monkeypatch.setattr(
        foundry,
        "get_hosted_responses_client",
        lambda: responses,
    )

    foundry.invoke_hosted_agent(
        conversation_id="conversation-1",
        user_id="user-1",
        message="OK",
        previous_response_id="response-1",
    )

    assert responses.kwargs["previous_response_id"] == "response-1"
    assert json.loads(responses.kwargs["input"]) == {
        "conversation_id": "conversation-1",
        "message": "OK",
    }


def test_extracts_nested_chat_output_text():
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "この旅程でよろしいですか？",
                    }
                ],
            }
        ]
    }

    assert (
        hosted_agent._extract_output_text(response)
        == "この旅程でよろしいですか？"
    )


def test_extracts_structured_display_from_workflow_approval_prompt():
    message, data = hosted_agent._extract_hitl_display(
        "申請を送信しますか？\n\n```json\n"
        '{"application_text":"申請書","policy_result":"規程適合"}'
        "\n```"
    )

    assert message == "申請を送信しますか？"
    assert data == {
        "application_text": "申請書",
        "policy_result": "規程適合",
    }


def test_hosted_approval_resume_sends_native_mcp_response(monkeypatch):
    responses = _CaptureResponses()
    monkeypatch.setattr(
        foundry,
        "get_hosted_responses_client",
        lambda: responses,
    )

    foundry.invoke_hosted_agent(
        conversation_id="conversation-1",
        user_id="user-1",
        previous_response_id="response-1",
        approval_request_id="approval-request-1",
        approve=True,
    )

    assert responses.kwargs["input"] == [
        {
            "type": "mcp_approval_response",
            "approval_request_id": "approval-request-1",
            "approve": True,
        }
    ]
    assert responses.kwargs["previous_response_id"] == "response-1"


def test_process_message_persists_native_mcp_approval_request(monkeypatch):
    updates = []
    emitted = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "scenario": "agent_framework_workflow",
                "foundry_response_id": "response-1",
                "pending_request": None,
            }

        async def update(self, conversation_id, **changes):
            updates.append(changes)

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            emitted.append((event_type, data))

    class Response:
        def model_dump(self, **kwargs):
            return {
                "id": "response-2",
                "status": "completed",
                "output": [
                    {
                        "type": "mcp_approval_request",
                        "id": "approval-request-1",
                        "name": "submit_travel_request_with_approval",
                        "server_label": "travel-request-submission",
                        "arguments": '{"approval_id":"grant-1"}',
                    }
                ],
            }

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "get_event_store", Events)
    monkeypatch.setattr(
        hosted_agent,
        "invoke_hosted_agent",
        lambda **kwargs: Response(),
    )

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="お願いします",
            message_id="message-1",
            idempotency_key="key-1",
        )
    )

    assert updates == [
        {
            "status": "awaiting_input",
            "foundry_response_id": "response-2",
            "pending_request": {
                "type": "mcp_approval",
                "approval_request_id": "approval-request-1",
            },
            "active_message": None,
            "processing_lease_until": None,
        }
    ]
    assert emitted == [
        (
            "hitl_request",
            {
                "type": "submit_confirmation",
                "message": "MCPツールの実行を承認しますか？",
                "data": {
                    "approval_request_id": "approval-request-1",
                    "tool_name": "submit_travel_request_with_approval",
                    "server_label": "travel-request-submission",
                    "arguments": {"approval_id": "grant-1"},
                    "application_text": "",
                },
            },
        )
    ]


def test_process_message_resumes_matching_native_mcp_approval(monkeypatch):
    invocations = []
    emitted = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "scenario": "agent_framework_workflow",
                "foundry_response_id": "response-1",
                "pending_request": {
                    "type": "mcp_approval",
                    "approval_request_id": "approval-request-1",
                },
            }

        async def update(self, conversation_id, **changes):
            assert changes["status"] == "ready"
            assert changes["pending_request"] is None

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            emitted.append((event_type, data))

    class Response:
        def model_dump(self, **kwargs):
            return {
                "id": "response-2",
                "status": "completed",
                "output_text": "出張申請を登録しました。",
                "output": [],
            }

    def fake_hosted(**kwargs):
        invocations.append(kwargs)
        return Response()

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "get_event_store", Events)
    monkeypatch.setattr(hosted_agent, "invoke_hosted_agent", fake_hosted)

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="",
            message_id="message-1",
            idempotency_key="key-1",
            approval_request_id="approval-request-1",
            approve=True,
        )
    )

    assert invocations == [
        {
            "conversation_id": "conversation-1",
            "user_id": "user-1",
            "message": None,
            "previous_response_id": "response-1",
            "approval_request_id": "approval-request-1",
            "approve": True,
        }
    ]
    assert emitted == [
        (
            "agent_response",
            {
                "step": "agent_framework_workflow",
                "content": "出張申請を登録しました。",
            },
        )
    ]


def test_process_message_forwards_plain_user_input_without_callbacks(
    monkeypatch,
):
    invocations = []
    emitted = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "id": conversation_id,
                "user_id": user_id,
                "scenario": "agent_framework_workflow",
                "foundry_response_id": "response-1",
                "pending_request": {
                    "call_id": "call-1",
                    "request_id": "request-1",
                },
            }

        async def update(self, conversation_id, **changes):
            assert changes["status"] == "ready"
            assert changes["pending_request"] is None

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            emitted.append((event_type, data))

    class Response:
        output_text = "出張申請の送信をキャンセルしました。"

        def model_dump(self, **kwargs):
            return {
                "id": "response-2",
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

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="キャンセル",
            message_id="message-1",
            idempotency_key="key-1",
        )
    )

    assert invocations == [
        {
            "conversation_id": "conversation-1",
            "user_id": "user-1",
            "message": "キャンセル",
            "previous_response_id": "response-1",
            "approval_request_id": None,
            "approve": None,
        }
    ]
    assert emitted == [
        (
            "agent_response",
            {
                "step": "agent_framework_workflow",
                "content": "出張申請の送信をキャンセルしました。",
            },
        )
    ]


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


def test_process_message_rejects_function_call_response(monkeypatch):
    emitted = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "scenario": "agent_framework_workflow",
                "foundry_response_id": None,
            }

        async def update(self, conversation_id, **changes):
            assert changes["status"] == "failed"

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            emitted.append((event_type, data))

    class Response:
        def model_dump(self, **kwargs):
            return {
                "id": "response-1",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "request_info",
                    }
                ],
            }

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)
    monkeypatch.setattr(hosted_agent, "get_event_store", Events)
    monkeypatch.setattr(
        hosted_agent,
        "invoke_hosted_agent",
        lambda **kwargs: Response(),
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

    assert emitted[0][0] == "error"
    assert "unsupported callback" in emitted[0][1]["message"]


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

    assert processed == [
        {
            "conversation_id": "conversation-1",
            **message,
            "approval_request_id": None,
            "approve": None,
        }
    ]


def test_process_message_ignores_legacy_single_prompt_mode_fields(
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
                "submission_token": "legacy-token",
                "foundry_response_id": None,
                "pending_request": None,
            }

        async def update(self, conversation_id, **changes):
            assert conversation_id == "conversation-1"
            assert changes["status"] == "ready"
            assert changes["pending_request"] is None

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            assert conversation_id == "conversation-1"
            assert event_type == "agent_response"
            assert data == {
                "step": "single_prompt_agent",
                "content": "完了",
            }

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
            "conversation_id": "conversation-1",
            "message": "大阪へ出張",
            "previous_response_id": None,
            "approval_request_id": None,
            "approve": None,
        }
    ]


def test_single_prompt_clears_legacy_pending_request(monkeypatch):
    invocations = []
    emitted = []

    class Store:
        async def get_owned(self, conversation_id, user_id):
            return {
                "scenario": "single_prompt_agent",
                "foundry_response_id": "response-1",
                "pending_request": {
                    "call_id": "legacy-call",
                    "type": "plan_review",
                },
            }

        async def update(self, conversation_id, **changes):
            assert changes["status"] == "ready"
            assert changes["pending_request"] is None

    class Events:
        async def append(self, conversation_id, event_type, data, **kwargs):
            emitted.append((event_type, data))

    class Response:
        def model_dump(self, **kwargs):
            return {
                "id": "response-2",
                "status": "completed",
                "output_text": "続行しました。",
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

    asyncio.run(
        hosted_agent.process_message(
            conversation_id="conversation-1",
            user_id="user-1",
            content="はい",
            message_id="message-1",
            idempotency_key="key-1",
        )
    )

    assert invocations == [
        {
            "conversation_id": "conversation-1",
            "message": "はい",
            "previous_response_id": "response-1",
            "approval_request_id": None,
            "approve": None,
        }
    ]
    assert emitted == [
        (
            "agent_response",
            {
                "step": "single_prompt_agent",
                "content": "続行しました。",
            },
        )
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
            assert changes["status"] == "ready"

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


def test_legacy_mode_field_does_not_change_scenario_route():
    assert hosted_agent._conversation_route(
        {
            "scenario": "single_prompt_agent",
            "interaction_mode": "playground",
        }
    ) == "single_prompt_agent"


def test_skips_message_claimed_by_another_replica(monkeypatch):
    class Store:
        async def claim_pending_message(self, conversation_id):
            assert conversation_id == "conversation-1"
            return None

    monkeypatch.setattr(hosted_agent, "get_conversation_store", Store)

    asyncio.run(hosted_agent.process_queued_message("conversation-1"))
