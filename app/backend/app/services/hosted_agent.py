"""Thin BFF adapter for Foundry Agent responses and UI events."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from azure.cosmos.exceptions import CosmosHttpResponseError

from app.services.cosmos import (
    get_conversation_store,
    get_event_store,
)
from app.services.foundry import (
    invoke_hosted_agent,
    invoke_single_prompt_agent,
    response_to_dict,
)

logger = logging.getLogger(__name__)



def _raise_for_response_error(response: dict[str, Any]) -> None:
    error = response.get("error")
    if not error and response.get("status") != "failed":
        return
    if isinstance(error, dict):
        code = str(error.get("code", "server_error"))
        message = str(error.get("message", "Foundry Agent request failed"))
    else:
        code = "server_error"
        message = str(error or "Foundry Agent request failed")
    raise RuntimeError(f"Foundry Agent failed ({code}): {message}")


def _loads_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Expected a JSON object")


def _extract_request_info(
    response: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for item in response.get("output", []):
        if item.get("type") != "function_call" or item.get("name") != "request_info":
            continue

        arguments = _loads_object(item.get("arguments", "{}"))
        request_event = _loads_object(
            arguments.get("request_event", arguments)
        )
        payload = _loads_object(request_event.get("data", request_event))
        request_type = str(payload.get("type", ""))
        if not request_type:
            raise ValueError("HITL request has no type")
        message = str(payload.get("message") or "入力を確認してください。")
        event_data = payload.get("data")
        if not isinstance(event_data, dict):
            event_data = {}

        pending = {
            "call_id": str(item.get("call_id", "")),
            "request_id": str(
                arguments.get("request_id") or item.get("call_id", "")
            ),
        }
        if not pending["call_id"]:
            raise ValueError("request_info function call has no call_id")
        event = {
            "type": request_type,
            "message": message,
            "data": event_data,
        }
        return pending, event
    return None


def _conversation_route(conversation: dict[str, Any]) -> str:
    scenario = str(
        conversation.get("scenario") or "agent_framework_workflow"
    )
    if scenario not in {
        "agent_framework_workflow",
        "single_prompt_agent",
    }:
        raise ValueError(f"Unsupported conversation scenario: {scenario}")
    return scenario


def _invoke_for_conversation(
    *,
    scenario: str,
    conversation_id: str,
    user_id: str,
    message: str | None = None,
    previous_response_id: str | None = None,
    function_call_id: str | None = None,
    function_output: str | dict[str, Any] | None = None,
):
    if scenario == "agent_framework_workflow":
        return invoke_hosted_agent(
            conversation_id=conversation_id,
            user_id=user_id,
            message=message,
            previous_response_id=previous_response_id,
            function_call_id=function_call_id,
            function_output=function_output,
        )
    if scenario == "single_prompt_agent":
        return invoke_single_prompt_agent(
            conversation_id=conversation_id,
            message=message,
            previous_response_id=previous_response_id,
        )
    raise ValueError(f"Unsupported conversation scenario: {scenario}")


async def process_message(
    *,
    conversation_id: str,
    user_id: str,
    content: str,
    message_id: str,
    idempotency_key: str,
) -> None:
    """Invoke or resume the stored scenario and persist normalized UI events."""
    conversations = get_conversation_store()
    events = get_event_store()
    try:
        conversation = await conversations.get_owned(conversation_id, user_id)
        if not conversation:
            raise LookupError("Conversation not found")

        scenario = _conversation_route(conversation)
        pending = conversation.get("pending_request")
        if scenario == "single_prompt_agent" and pending:
            raise RuntimeError(
                "This conversation uses the previous HITL format. "
                "Reset it to start a Prompt Agent-only conversation."
            )
        if pending:
            response = await asyncio.to_thread(
                _invoke_for_conversation,
                scenario=scenario,
                conversation_id=conversation_id,
                user_id=user_id,
                previous_response_id=conversation.get("foundry_response_id"),
                function_call_id=pending["call_id"],
                function_output=content.strip(),
            )
        else:
            response = await asyncio.to_thread(
                _invoke_for_conversation,
                scenario=scenario,
                conversation_id=conversation_id,
                user_id=user_id,
                message=content,
                previous_response_id=conversation.get("foundry_response_id"),
            )

        response_data = response_to_dict(response)
        _raise_for_response_error(response_data)
        response_id = str(response_data.get("id", ""))
        output = str(
            response_data.get("output_text")
            or getattr(response, "output_text", "")
            or ""
        ).strip()

        if scenario == "single_prompt_agent":
            unexpected_callbacks = {
                "function_call",
                "mcp_approval_request",
            }.intersection(
                str(item.get("type", ""))
                for item in response_data.get("output", [])
            )
            if unexpected_callbacks:
                raise RuntimeError(
                    "Single Prompt Agent returned an unsupported callback: "
                    + ", ".join(sorted(unexpected_callbacks))
                )
            if not output:
                raise RuntimeError("Single Prompt Agent returned no message")
            await conversations.update(
                conversation_id,
                status="ready",
                foundry_response_id=response_id,
                pending_request=None,
                active_message=None,
                processing_lease_until=None,
            )
            await events.append(
                conversation_id,
                "agent_response",
                {"step": "single_prompt_agent", "content": output},
                message_id=message_id,
            )
            return

        request_info = _extract_request_info(response_data)
        if request_info:
            next_pending, hitl_event = request_info
            await conversations.update(
                conversation_id,
                status="awaiting_input",
                foundry_response_id=response_id,
                pending_request=next_pending,
                active_message=None,
                processing_lease_until=None,
            )
            await events.append(
                conversation_id,
                "hitl_request",
                hitl_event,
                message_id=message_id,
            )
            return

        await conversations.update(
            conversation_id,
            status="completed",
            foundry_response_id=response_id,
            pending_request=None,
            active_message=None,
            processing_lease_until=None,
        )
        await events.append(
            conversation_id,
            "complete",
            {"output": output},
            message_id=message_id,
        )
    except Exception as exc:
        logger.exception("Foundry Agent processing failed for %s", conversation_id)
        await conversations.update(
            conversation_id,
            status="failed",
            active_message=None,
            processing_lease_until=None,
        )
        await events.append(
            conversation_id,
            "error",
            {"message": str(exc)},
            message_id=message_id,
            idempotency_key=idempotency_key,
        )


async def process_queued_message(conversation_id: str) -> None:
    """Claim and process one durable conversation work item."""
    conversations = get_conversation_store()
    message = await conversations.claim_pending_message(conversation_id)
    if not message:
        return
    await process_message(
        conversation_id=conversation_id,
        user_id=message["user_id"],
        content=message["content"],
        message_id=message["message_id"],
        idempotency_key=message["idempotency_key"],
    )


async def recover_pending_messages(poll_interval_seconds: int = 60) -> None:
    """Reschedule work left behind by an interrupted BFF replica."""
    conversations = get_conversation_store()
    while True:
        try:
            conversation_ids = await conversations.list_pending_conversation_ids()
        except CosmosHttpResponseError:
            logger.exception("Failed to scan durable conversation work")
            await asyncio.sleep(poll_interval_seconds)
            continue
        for conversation_id in conversation_ids:
            asyncio.create_task(
                process_queued_message(conversation_id),
                name=f"recover-{conversation_id}-{uuid.uuid4().hex[:8]}",
            )
        await asyncio.sleep(poll_interval_seconds)
