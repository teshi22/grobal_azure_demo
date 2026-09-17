"""Foundry-hosted MCP tools for preparing and submitting travel requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent_framework import Agent, AgentResponse, AgentSession, Content, Message
from agent_framework.foundry import FoundryChatClient
from azure.ai.projects.models import (
    MCPTool,
    MCPToolFilter,
    MCPToolRequireApproval,
)


@dataclass(frozen=True)
class PendingMCPApproval:
    request_id: str
    tool_name: str
    arguments: str | dict[str, Any]
    server_label: str
    session: dict[str, Any]


class MCPSubmissionAgent:
    """Call the submission MCP server through Foundry's hosted MCP runtime."""

    def __init__(
        self,
        *,
        project_endpoint: str,
        model: str,
        server_url: str,
        connection_id: str,
        credential: Any,
    ) -> None:
        client = FoundryChatClient(
            project_endpoint=project_endpoint,
            model=model,
            credential=credential,
        )
        approval_policy = MCPToolRequireApproval(
            always=MCPToolFilter(
                tool_names=["submit_travel_request_with_approval"]
            ),
            never=MCPToolFilter(
                tool_names=["prepare_travel_request_submission"]
            ),
        )
        tool = MCPTool(
            server_label="travel-request-submission",
            server_url=server_url,
            project_connection_id=connection_id,
            allowed_tools=[
                "prepare_travel_request_submission",
                "submit_travel_request_with_approval",
            ],
            require_approval=approval_policy,
        )
        self._prepare_agent = Agent(
            client=client,
            name="travel-request-preparer",
            instructions=(
                "Call prepare_travel_request_submission exactly once with the "
                "provided JSON object. Return the MCP tool result unchanged."
            ),
            tools=[tool],
        )
        self._submit_agent = Agent(
            client=client,
            name="travel-request-submitter",
            instructions=(
                "Call submit_travel_request_with_approval exactly once with "
                "the provided approval_id. Do not retry a denied call. Return "
                "the MCP tool result unchanged."
            ),
            tools=[tool],
        )

    async def prepare(self, arguments: dict[str, Any]) -> dict[str, Any]:
        response = await self._prepare_agent.run(
            json.dumps(arguments, ensure_ascii=False),
            options={"tool_choice": "required"},
        )
        payload = _tool_payload(response)
        if not payload.get("success"):
            raise RuntimeError(
                str(payload.get("message", "Submission preparation failed"))
            )
        return payload

    async def request_submission(
        self,
        approval_id: str,
    ) -> PendingMCPApproval:
        session = self._submit_agent.create_session()
        response = await self._submit_agent.run(
            json.dumps({"approval_id": approval_id}, ensure_ascii=False),
            session=session,
            options={"tool_choice": "required"},
        )
        approvals = [
            content
            for message in response.messages
            for content in message.contents
            if content.type == "function_approval_request"
        ]
        if len(approvals) != 1:
            raise RuntimeError(
                "Hosted MCP submit did not return exactly one approval request"
            )
        approval = approvals[0]
        function_call = approval.function_call
        if (
            not approval.id
            or function_call is None
            or not function_call.name
        ):
            raise RuntimeError("Hosted MCP approval request is incomplete")
        server_label = str(
            function_call.additional_properties.get("server_label", "")
        )
        return PendingMCPApproval(
            request_id=approval.id,
            tool_name=function_call.name,
            arguments=function_call.arguments or {},
            server_label=server_label,
            session=session.to_dict(),
        )

    async def resolve_submission(
        self,
        pending: PendingMCPApproval,
        approved: bool,
    ) -> dict[str, Any]:
        function_call = Content.from_function_call(
            call_id=pending.request_id,
            name=pending.tool_name,
            arguments=pending.arguments,
            additional_properties={"server_label": pending.server_label},
        )
        approval_request = Content.from_function_approval_request(
            id=pending.request_id,
            function_call=function_call,
        )
        response = await self._submit_agent.run(
            Message(
                role="user",
                contents=[
                    approval_request.to_function_approval_response(approved)
                ],
            ),
            session=AgentSession.from_dict(pending.session),
            options={"tool_choice": "none"},
        )
        if not approved:
            return {
                "success": True,
                "submitted": False,
                "cancelled": True,
                "message": "出張申請の送信をキャンセルしました。",
            }
        payload = _tool_payload(response)
        if not payload.get("success"):
            raise RuntimeError(
                str(payload.get("message", "Travel request submission failed"))
            )
        return payload


def _tool_payload(response: AgentResponse) -> dict[str, Any]:
    candidates: list[Any] = []
    for message in response.messages:
        for content in message.contents:
            if content.type == "mcp_server_tool_result":
                output = content.output
                if isinstance(output, list):
                    candidates.extend(output)
                else:
                    candidates.append(output)
    candidates.extend(
        content.text
        for message in response.messages
        for content in message.contents
        if content.type == "text" and content.text
    )
    for candidate in candidates:
        value = candidate.text if isinstance(candidate, Content) else candidate
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    raise RuntimeError("Hosted MCP tool returned no JSON result")
