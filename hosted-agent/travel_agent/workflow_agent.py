"""Workflow agent adapter that supports both BFF callbacks and direct chat."""

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


class ChatCompatibleWorkflowAgent(WorkflowAgent):
    """Expose HITL requests as text and accept a plain reply from chat clients."""

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
                message.contents.insert(
                    0,
                    Content.from_text(text=self._request_prompt(event)),
                )
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
                update.contents.insert(
                    0,
                    Content.from_text(text=self._request_prompt(event)),
                )
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
            if not reply:
                raise ValueError("The HITL reply must not be empty")
            return {next(iter(pending)): reply}

        if contents and all(
            content.type == "function_approval_response"
            for content in contents
        ):
            responses: dict[str, str] = {}
            for content in contents:
                if not content.id:
                    raise ValueError("The approval response has no request ID")
                responses[content.id] = (
                    "OK" if content.approved else "キャンセル"
                )
            return responses

        return super()._extract_function_responses(
            input_messages,
            pending_requests,
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
