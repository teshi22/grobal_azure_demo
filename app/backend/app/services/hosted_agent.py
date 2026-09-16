"""BFF orchestration for Foundry Hosted Agent responses and HITL."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from azure.cosmos.exceptions import CosmosHttpResponseError

from app.services.cosmos import (
    get_approval_grant_store,
    get_conversation_store,
    get_event_store,
)
from app.services.foundry import (
    invoke_hosted_agent,
    invoke_playground_agent,
    response_to_dict,
)

logger = logging.getLogger(__name__)

_APPROVAL_WORDS = frozenset(
    {"ok", "yes", "y", "はい", "確定", "進めて", "大丈夫", "承認"}
)
_REQUEST_INFO_TYPES = frozenset(
    {
        "clarification",
        "request_confirmation",
        "plan_review",
        "submit_confirmation",
    }
)


def _raise_for_response_error(response: dict[str, Any]) -> None:
    error = response.get("error")
    if not error and response.get("status") != "failed":
        return
    if isinstance(error, dict):
        code = str(error.get("code", "server_error"))
        message = str(error.get("message", "Hosted Agent request failed"))
    else:
        code = "server_error"
        message = str(error or "Hosted Agent request failed")
    raise RuntimeError(f"Hosted Agent failed ({code}): {message}")


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
    *,
    allow_submit_confirmation: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for item in response.get("output", []):
        if item.get("type") != "function_call" or item.get("name") != "request_info":
            continue

        arguments = _loads_object(item.get("arguments", "{}"))
        if "request_event" in arguments:
            request_event = _loads_object(arguments["request_event"])
            payload = _loads_object(request_event.get("data", request_event))
        else:
            payload = arguments
        request_type = str(payload.get("type", ""))
        if request_type not in _REQUEST_INFO_TYPES:
            raise ValueError(f"Unsupported HITL request type: {request_type}")
        if request_type == "submit_confirmation" and not allow_submit_confirmation:
            raise ValueError(
                "Submission confirmation is not supported in playground mode"
            )

        message = str(
            payload.get("message")
            or payload.get("question")
            or "入力を確認してください。"
        )
        event_data = payload.get("data")
        if not isinstance(event_data, dict):
            event_data = {}
        if request_type == "clarification":
            event_data = {
                **event_data,
                "question": message,
                "missing_fields": payload.get(
                    "missing_fields",
                    event_data.get("missing_fields", []),
                ),
            }
        elif request_type == "request_confirmation":
            if not event_data:
                event_data = payload.get("fields", {})
        elif request_type == "plan_review":
            if not event_data:
                event_data = _loads_object(payload.get("plan_json", "{}"))
        elif request_type == "submit_confirmation":
            if not event_data:
                event_data = {
                    "application_text": payload.get("application_text", ""),
                    "plan": _loads_object(payload.get("plan_json", "{}")),
                    "policy_result": payload.get("policy_narrative", ""),
                    "plan_hash": payload.get("plan_hash", ""),
                }

        pending = {
            "call_id": str(item.get("call_id", "")),
            "request_id": str(
                arguments.get("request_id") or item.get("call_id", "")
            ),
            "type": request_type,
            "payload": {**payload, "data": event_data},
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


def _approved(content: str) -> bool:
    return content.strip().lower() in _APPROVAL_WORDS


def _conversation_route(conversation: dict[str, Any]) -> tuple[str, str]:
    scenario = str(
        conversation.get("scenario") or "agent_framework_workflow"
    )
    interaction_mode = str(
        conversation.get("interaction_mode") or "submission"
    )
    if scenario not in {
        "agent_framework_workflow",
        "single_prompt_agent",
    }:
        raise ValueError(f"Unsupported conversation scenario: {scenario}")
    if interaction_mode not in {"submission", "playground"}:
        raise ValueError(
            f"Unsupported conversation interaction mode: {interaction_mode}"
        )
    if scenario == "single_prompt_agent" and interaction_mode == "submission":
        raise ValueError(
            "single_prompt_agent does not support submission mode"
        )
    return scenario, interaction_mode


def _invoke_for_conversation(
    *,
    scenario: str,
    interaction_mode: str,
    conversation_id: str,
    user_id: str,
    message: str | None = None,
    previous_response_id: str | None = None,
    function_call_id: str | None = None,
    function_output: dict[str, Any] | None = None,
):
    if interaction_mode == "submission":
        return invoke_hosted_agent(
            conversation_id=conversation_id,
            user_id=user_id,
            message=message,
            previous_response_id=previous_response_id,
            function_call_id=function_call_id,
            function_output=function_output,
        )
    return invoke_playground_agent(
        scenario=scenario,
        conversation_id=conversation_id,
        message=message,
        previous_response_id=previous_response_id,
        function_call_id=function_call_id,
        function_output=function_output,
    )


async def _build_function_output(
    *,
    content: str,
    pending: dict[str, Any],
    user_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    request_type = pending["type"]
    approved = _approved(content)

    if request_type == "clarification":
        return {"answer": content.strip()}
    if request_type == "request_confirmation":
        return {
            "confirmed": approved,
            "revision": "" if approved else content.strip(),
        }
    if request_type == "plan_review":
        return {
            "approved": approved,
            "feedback": "" if approved else content.strip(),
        }
    if request_type != "submit_confirmation":
        raise ValueError(f"Unsupported pending request: {request_type}")

    if not approved:
        return {
            "approved": False,
            "approval_grant_id": "",
            "idempotency_key": "",
        }

    payload_data = pending.get("payload", {}).get("data", {})
    plan_hash = str(payload_data.get("plan_hash", ""))
    if not plan_hash:
        raise ValueError("Submission confirmation has no plan hash")
    grant = await get_approval_grant_store().issue(
        user_id=user_id,
        conversation_id=conversation_id,
        call_id=pending["call_id"],
        plan_hash=plan_hash,
    )
    return {
        "approved": True,
        "approval_grant_id": grant["id"],
        "idempotency_key": grant["idempotency_key"],
    }


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

        scenario, interaction_mode = _conversation_route(conversation)
        pending = conversation.get("pending_request")
        if pending:
            function_output = await _build_function_output(
                content=content,
                pending=pending,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            response = await asyncio.to_thread(
                _invoke_for_conversation,
                scenario=scenario,
                interaction_mode=interaction_mode,
                conversation_id=conversation_id,
                user_id=user_id,
                previous_response_id=conversation.get("foundry_response_id"),
                function_call_id=pending["call_id"],
                function_output=function_output,
            )
        else:
            response = await asyncio.to_thread(
                _invoke_for_conversation,
                scenario=scenario,
                interaction_mode=interaction_mode,
                conversation_id=conversation_id,
                user_id=user_id,
                message=content,
                previous_response_id=conversation.get("foundry_response_id"),
            )

        response_data = response_to_dict(response)
        _raise_for_response_error(response_data)
        response_id = str(response_data.get("id", ""))
        request_info = _extract_request_info(
            response_data,
            allow_submit_confirmation=interaction_mode == "submission",
        )
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

        output = str(
            response_data.get("output_text")
            or getattr(response, "output_text", "")
            or ""
        )
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
