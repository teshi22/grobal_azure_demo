"""Workflow adapter for chat HITL and native MCP approval."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from agent_framework import (
    AgentResponse,
    AgentResponseUpdate,
    Content,
    Message,
    WorkflowAgent,
    WorkflowEvent,
)

from .models import SubmissionApprovalRequest


class MCPApprovalWorkflowAgent(WorkflowAgent):
    """Expose conversational HITL and native MCP submission approval."""

    def _convert_workflow_events_to_agent_response(
        self,
        response_id: str,
        output_events: list[WorkflowEvent[Any]],
    ) -> AgentResponse:
        response = super()._convert_workflow_events_to_agent_response(
            response_id,
            output_events,
        )
        for message in response.messages:
            event = message.raw_representation
            if isinstance(event, WorkflowEvent) and event.type == "request_info":
                message.contents = self._request_contents(event)
        return response

    def _convert_workflow_event_to_agent_response_updates(
        self,
        response_id: str,
        event: WorkflowEvent[Any],
    ) -> list[AgentResponseUpdate]:
        updates = super()._convert_workflow_event_to_agent_response_updates(
            response_id,
            event,
        )
        if event.type == "request_info":
            for update in updates:
                update.contents = self._request_contents(event)
        return updates

    def _extract_function_responses(
        self,
        input_messages: Sequence[Message],
        pending_requests: Mapping[str, WorkflowEvent[Any]] | None = None,
    ) -> dict[str, Any]:
        pending = pending_requests or {}
        contents = [
            content
            for message in input_messages
            for content in message.contents
        ]
        if contents and all(
            content.type == "function_approval_response"
            for content in contents
        ):
            if len(pending) != 1 or len(contents) != 1:
                raise ValueError(
                    "MCP approval requires exactly one pending request"
                )
            request_id, event = next(iter(pending.items()))
            request = event.data
            if not isinstance(request, SubmissionApprovalRequest):
                raise ValueError(
                    "MCP approval response does not match the pending request"
                )
            approval = contents[0]
            if approval.id != request.approval_request_id:
                raise ValueError("MCP approval response ID does not match")
            if not isinstance(approval.approved, bool):
                raise ValueError("MCP approval response has no decision")
            return {request_id: approval.approved}

        if contents and all(content.type == "text" for content in contents):
            if len(pending) != 1:
                raise ValueError(
                    "A plain chat reply requires exactly one pending HITL request"
                )
            if isinstance(
                next(iter(pending.values())).data,
                SubmissionApprovalRequest,
            ):
                raise ValueError(
                    "Submission requires the native MCP approval control"
                )
            reply = " ".join(content.text or "" for content in contents).strip()
            try:
                envelope = json.loads(reply)
            except json.JSONDecodeError:
                envelope = None
            if isinstance(envelope, dict) and isinstance(
                envelope.get("message"),
                str,
            ):
                reply = envelope["message"].strip()
            if not reply:
                raise ValueError("The HITL reply must not be empty")
            return {next(iter(pending)): reply}

        raise ValueError(
            "HITL responses must be chat text or native MCP approval"
        )

    @classmethod
    def _request_contents(
        cls,
        event: WorkflowEvent[Any],
    ) -> list[Content]:
        request = event.data
        contents = [Content.from_text(text=cls._request_prompt(event))]
        if not isinstance(request, SubmissionApprovalRequest):
            return contents
        function_call = Content.from_function_call(
            call_id=request.approval_request_id,
            name=request.tool_name,
            arguments=request.tool_arguments,
            additional_properties={"server_label": request.server_label},
        )
        contents.append(
            Content.from_function_approval_request(
                id=request.approval_request_id,
                function_call=function_call,
            )
        )
        return contents

    @staticmethod
    def _request_prompt(event: WorkflowEvent[Any]) -> str:
        request = event.data
        message = str(
            getattr(request, "message", "")
            or getattr(request, "question", "")
            or "入力を確認してください。"
        ).strip()
        data = getattr(request, "data", None)
        if not isinstance(data, dict) or not data:
            return message

        details = json.dumps(data, ensure_ascii=False, indent=2)
        return f"{message}\n\n```json\n{details}\n```"
