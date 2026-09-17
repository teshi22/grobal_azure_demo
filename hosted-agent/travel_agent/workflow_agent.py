"""Workflow agent adapter that exposes HITL as ordinary chat."""

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


class ChatOnlyWorkflowAgent(WorkflowAgent):
    """Expose HITL requests and responses without external function calls."""

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
                message.contents = [
                    Content.from_text(text=self._request_prompt(event))
                ]
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
                update.contents = [
                    Content.from_text(text=self._request_prompt(event))
                ]
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
        if contents and all(content.type == "text" for content in contents):
            if len(pending) != 1:
                raise ValueError(
                    "A plain chat reply requires exactly one pending HITL request"
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
            "HITL responses must be sent as ordinary chat text"
        )

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
