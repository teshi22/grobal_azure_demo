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


def _extract_output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    return "\n".join(
        str(content.get("text", "")).strip()
        for item in response.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") == "output_text"
        and str(content.get("text", "")).strip()
    )


def _extract_hitl_display(output: str) -> tuple[str, dict[str, Any]]:
    marker = "```json"
    if marker not in output:
        return output, {}
    message, _, remainder = output.partition(marker)
    payload, separator, _ = remainder.partition("```")
    if not separator:
        return output, {}
    try:
        data = json.loads(payload.strip())
    except json.JSONDecodeError:
        return output, {}
    if not isinstance(data, dict):
        return output, {}
    return message.strip(), data


def _extract_mcp_approval_request(
    response: dict[str, Any],
    output: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    approvals = [
        item
        for item in response.get("output", [])
        if item.get("type") == "mcp_approval_request"
    ]
    if not approvals:
        return None
    if len(approvals) != 1:
        raise RuntimeError("Agent returned multiple MCP approval requests")
    approval = approvals[0]
    approval_request_id = str(approval.get("id", "")).strip()
    if not approval_request_id:
        raise RuntimeError("MCP approval request has no ID")
    arguments = approval.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "MCP approval request has invalid arguments"
            ) from exc
    if not isinstance(arguments, dict):
        raise RuntimeError("MCP approval request arguments must be an object")
    message, display_data = _extract_hitl_display(output)
    pending = {
        "type": "mcp_approval",
        "approval_request_id": approval_request_id,
    }
    event = {
        "type": "submit_confirmation",
        "message": message or "MCPツールの実行を承認しますか？",
        "data": {
            **display_data,
            "approval_request_id": approval_request_id,
            "tool_name": str(approval.get("name", "")),
            "server_label": str(approval.get("server_label", "")),
            "arguments": arguments,
            "application_text": str(
                display_data.get("application_text") or output
            ),
        },
    }
    return pending, event


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
    approval_request_id: str | None = None,
    approve: bool | None = None,
):
    if scenario == "agent_framework_workflow":
        return invoke_hosted_agent(
            conversation_id=conversation_id,
            user_id=user_id,
            message=message,
            previous_response_id=previous_response_id,
            approval_request_id=approval_request_id,
            approve=approve,
        )
    if scenario == "single_prompt_agent":
        return invoke_single_prompt_agent(
            conversation_id=conversation_id,
            message=message,
            previous_response_id=previous_response_id,
            approval_request_id=approval_request_id,
            approve=approve,
        )
    raise ValueError(f"Unsupported conversation scenario: {scenario}")


async def process_message(
    *,
    conversation_id: str,
    user_id: str,
    content: str,
    message_id: str,
    idempotency_key: str,
    approval_request_id: str | None = None,
    approve: bool | None = None,
) -> None:
    """Invoke or resume the stored scenario and persist normalized UI events."""
    conversations = get_conversation_store()
    events = get_event_store()
    try:
        conversation = await conversations.get_owned(conversation_id, user_id)
        if not conversation:
            raise LookupError("Conversation not found")

        scenario = _conversation_route(conversation)
        pending = conversation.get("pending_request") or {}
        if pending.get("type") == "mcp_approval":
            expected_id = str(pending.get("approval_request_id", ""))
            if (
                approval_request_id != expected_id
                or not isinstance(approve, bool)
            ):
                raise RuntimeError(
                    "Use the approval control for the pending MCP tool call"
                )
            response = await asyncio.to_thread(
                _invoke_for_conversation,
                scenario=scenario,
                conversation_id=conversation_id,
                user_id=user_id,
                previous_response_id=conversation.get(
                    "foundry_response_id"
                ),
                approval_request_id=approval_request_id,
                approve=approve,
            )
        else:
            if approval_request_id is not None or approve is not None:
                raise RuntimeError("There is no pending MCP approval request")
            response = await asyncio.to_thread(
                _invoke_for_conversation,
                scenario=scenario,
                conversation_id=conversation_id,
                user_id=user_id,
                message=content,
                previous_response_id=conversation.get(
                    "foundry_response_id"
                ),
            )

        response_data = response_to_dict(response)
        _raise_for_response_error(response_data)
        response_id = str(response_data.get("id", ""))
        output = _extract_output_text(response_data)
        unexpected_callbacks = {
            "function_call",
        }.intersection(
            str(item.get("type", ""))
            for item in response_data.get("output", [])
        )
        if unexpected_callbacks:
            raise RuntimeError(
                "Agent returned an unsupported callback: "
                + ", ".join(sorted(unexpected_callbacks))
            )
        if not output:
            approval_request = _extract_mcp_approval_request(
                response_data,
                output,
            )
            if not approval_request:
                raise RuntimeError("Agent returned no message")
        else:
            approval_request = _extract_mcp_approval_request(
                response_data,
                output,
            )
        if approval_request:
            next_pending, hitl_event = approval_request
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
            status="ready",
            foundry_response_id=response_id,
            pending_request=None,
            active_message=None,
            processing_lease_until=None,
        )
        await events.append(
            conversation_id,
            "agent_response",
            {"step": scenario, "content": output},
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
        approval_request_id=message.get("approval_request_id"),
        approve=message.get("approve"),
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
