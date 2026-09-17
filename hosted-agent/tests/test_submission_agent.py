import asyncio

import pytest
from agent_framework import AgentResponse, AgentSession, Content, Message

from travel_agent.submission_agent import (
    MCPSubmissionAgent,
    PendingMCPApproval,
    _tool_payload,
)


class _FakeAgent:
    def __init__(self, response: AgentResponse):
        self.response = response
        self.calls = []

    def create_session(self) -> AgentSession:
        return AgentSession(
            session_id="session-1",
            service_session_id="response-1",
        )

    async def run(self, message, **kwargs):
        self.calls.append((message, kwargs))
        return self.response


def _agent_with_submit_response(response: AgentResponse) -> MCPSubmissionAgent:
    agent = object.__new__(MCPSubmissionAgent)
    agent._submit_agent = _FakeAgent(response)
    return agent


def test_request_submission_returns_native_mcp_approval():
    function_call = Content.from_function_call(
        call_id="approval-request-1",
        name="submit_travel_request_with_approval",
        arguments={"approval_id": "grant-1"},
        additional_properties={
            "server_label": "travel-request-submission",
        },
    )
    response = AgentResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_approval_request(
                        id="approval-request-1",
                        function_call=function_call,
                    )
                ],
            )
        ]
    )
    agent = _agent_with_submit_response(response)

    pending = asyncio.run(agent.request_submission("grant-1"))

    assert pending.request_id == "approval-request-1"
    assert pending.tool_name == "submit_travel_request_with_approval"
    assert pending.arguments == {"approval_id": "grant-1"}
    assert pending.server_label == "travel-request-submission"
    assert pending.session["service_session_id"] == "response-1"
    _, kwargs = agent._submit_agent.calls[0]
    assert kwargs["options"] == {"tool_choice": "required"}


@pytest.mark.parametrize("approved", [True, False])
def test_resolve_submission_uses_native_decision_and_never_retries(approved):
    response = AgentResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_mcp_server_tool_result(
                        "approval-request-1",
                        output={
                            "success": True,
                            "submitted": True,
                            "request_id": "request-1",
                        },
                    )
                ],
            )
        ]
    )
    agent = _agent_with_submit_response(response)
    pending = PendingMCPApproval(
        request_id="approval-request-1",
        tool_name="submit_travel_request_with_approval",
        arguments={"approval_id": "grant-1"},
        server_label="travel-request-submission",
        session=AgentSession(
            session_id="session-1",
            service_session_id="response-1",
        ).to_dict(),
    )

    result = asyncio.run(agent.resolve_submission(pending, approved))

    message, kwargs = agent._submit_agent.calls[0]
    approval_response = message.contents[0]
    assert approval_response.type == "function_approval_response"
    assert approval_response.id == "approval-request-1"
    assert approval_response.approved is approved
    assert kwargs["options"] == {"tool_choice": "none"}
    if approved:
        assert result["submitted"] is True
        assert result["request_id"] == "request-1"
    else:
        assert result["submitted"] is False
        assert result["cancelled"] is True


def test_tool_payload_reads_json_from_mcp_content():
    response = AgentResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_mcp_server_tool_result(
                        "call-1",
                        output=[
                            Content.from_text(
                                text='{"success":true,"approval_id":"grant-1"}'
                            )
                        ],
                    )
                ],
            )
        ]
    )

    assert _tool_payload(response) == {
        "success": True,
        "approval_id": "grant-1",
    }
