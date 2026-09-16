"""Validate a one-time approval grant and persist one travel request."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from tools.cosmos_client import (
    get_approval_grant_container,
    get_container,
    get_conversation_container,
)

logger = logging.getLogger(__name__)

_PROMPT_AGENT_FALLBACK_USER = "foundry-prompt-agent"


def _failure(message: str) -> dict:
    return {"success": False, "request_id": "", "message": message}


def _validate_arguments(arguments: dict) -> str | None:
    required_strings = (
        "application_text",
        "conversation_id",
        "approval_grant_id",
        "idempotency_key",
        "plan_hash",
    )
    for key in required_strings:
        if not isinstance(arguments.get(key), str) or not arguments[key].strip():
            return f"{key} is required"
    if not isinstance(arguments.get("application_data"), dict):
        return "application_data must be an object"
    return None


def _validate_prepare_arguments(arguments: dict) -> str | None:
    required_strings = (
        "application_text",
        "policy_result",
    )
    for key in required_strings:
        if not isinstance(arguments.get(key), str) or not arguments[key].strip():
            return f"{key} is required"
    if not isinstance(arguments.get("application_data"), dict):
        return "application_data must be an object"
    conversation_id = arguments.get("conversation_id")
    if conversation_id is not None and (
        not isinstance(conversation_id, str) or not conversation_id.strip()
    ):
        return "conversation_id must be a non-empty string when provided"
    return None


def _validate_direct_arguments(arguments: dict) -> str | None:
    if (
        not isinstance(arguments.get("approval_id"), str)
        or not arguments["approval_id"].strip()
    ):
        return "approval_id is required"
    if arguments.get("user_confirmed") is not True:
        return "user_confirmed must be true"
    return None


def _plan_hash(application_data: dict) -> str:
    serialized = json.dumps(
        application_data,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def submit_travel_request(arguments: dict) -> dict:
    validation_error = _validate_arguments(arguments)
    if validation_error:
        return _failure(validation_error)

    conversation_id = arguments["conversation_id"]
    grant_id = arguments["approval_grant_id"]
    idempotency_key = arguments["idempotency_key"]
    supplied_plan_hash = arguments["plan_hash"]
    application_data = arguments["application_data"]

    if _plan_hash(application_data) != supplied_plan_hash:
        return _failure("Travel plan does not match the approved plan")

    grants = get_approval_grant_container()
    requests = get_container()
    try:
        grant = await grants.read_item(item=grant_id, partition_key=grant_id)
    except CosmosResourceNotFoundError:
        return _failure("Approval grant was not found")

    if grant.get("conversation_id") != conversation_id:
        return _failure("Approval grant conversation mismatch")
    if grant.get("plan_hash") != supplied_plan_hash:
        return _failure("Approval grant plan mismatch")
    if grant.get("idempotency_key") != idempotency_key:
        return _failure("Approval grant idempotency mismatch")

    request_id = f"TR-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:12].upper()}"
    try:
        existing = await requests.read_item(
            item=request_id,
            partition_key=request_id,
        )
    except CosmosResourceNotFoundError:
        existing = None
    if existing:
        return {
            "success": True,
            "duplicate": True,
            "request_id": request_id,
            "submitted_at": existing["submitted_at"],
            "message": f"出張申請 {request_id} は登録済みです。",
        }

    if grant.get("status") != "issued":
        return _failure("Approval grant has already been consumed")
    expires_at = datetime.fromisoformat(str(grant["expires_at"]).replace("Z", "+00:00"))
    if expires_at <= datetime.now(timezone.utc):
        return _failure("Approval grant has expired")

    submitted_at = datetime.now(timezone.utc).isoformat()
    document = {
        "id": request_id,
        "request_id": request_id,
        "user_id": grant["user_id"],
        "conversation_id": conversation_id,
        "approval_grant_id": grant_id,
        "idempotency_key": idempotency_key,
        "plan_hash": supplied_plan_hash,
        "status": "submitted",
        "submitted_at": submitted_at,
        "application_text": arguments["application_text"],
        **application_data,
    }

    try:
        await requests.create_item(document, if_none_match="*")
    except CosmosHttpResponseError as exc:
        if exc.status_code != 409:
            raise

    grant["status"] = "consumed"
    grant["consumed_at"] = datetime.now(timezone.utc).isoformat()
    grant["request_id"] = request_id
    try:
        await grants.replace_item(
            item=grant_id,
            body=grant,
            etag=grant.get("_etag"),
            match_condition=MatchConditions.IfNotModified,
        )
    except CosmosHttpResponseError as exc:
        if exc.status_code != 412:
            raise
        current = await grants.read_item(item=grant_id, partition_key=grant_id)
        if current.get("request_id") != request_id:
            raise

    logger.info(
        "Travel request %s stored for user %s",
        request_id,
        grant["user_id"],
    )
    return {
        "success": True,
        "duplicate": False,
        "request_id": request_id,
        "submitted_at": submitted_at,
        "message": f"出張申請 {request_id} を登録しました。",
    }


async def prepare_travel_request_submission(arguments: dict) -> dict:
    """Store an immutable application draft before final user confirmation."""
    validation_error = _validate_prepare_arguments(arguments)
    if validation_error:
        return _failure(validation_error)

    conversation_id = str(arguments.get("conversation_id") or "").strip()
    user_id = _PROMPT_AGENT_FALLBACK_USER
    if conversation_id:
        conversations = get_conversation_container()
        try:
            conversation = await conversations.read_item(
                item=conversation_id,
                partition_key=conversation_id,
            )
        except CosmosResourceNotFoundError:
            return _failure("Conversation was not found")

        if conversation.get("scenario") != "single_prompt_agent":
            return _failure("Conversation is not a single Prompt Agent session")
        user_id = str(conversation.get("user_id") or "").strip()
        if not user_id:
            return _failure("Conversation has no owner")

    approval_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=10)
    application_data = arguments["application_data"]
    grants = get_approval_grant_container()
    grant = {
        "id": approval_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "plan_hash": _plan_hash(application_data),
        "idempotency_key": hashlib.sha256(
            f"prompt-agent:{approval_id}".encode("utf-8")
        ).hexdigest(),
        "approval_mode": "prompt_agent_mcp",
        "status": "awaiting_confirmation",
        "application_text": arguments["application_text"],
        "application_data": application_data,
        "policy_result": arguments["policy_result"],
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl": 600,
    }
    await grants.create_item(grant, if_none_match="*")
    return {
        "success": True,
        "approval_id": approval_id,
        "expires_at": expires_at.isoformat(),
        "message": "申請内容を固定しました。利用者の最終確認を取得してください。",
    }


async def submit_travel_request_with_approval(arguments: dict) -> dict:
    """Consume an MCP-managed approval after explicit user confirmation."""
    validation_error = _validate_direct_arguments(arguments)
    if validation_error:
        return _failure(validation_error)

    approval_id = arguments["approval_id"]
    grants = get_approval_grant_container()
    try:
        grant = await grants.read_item(
            item=approval_id,
            partition_key=approval_id,
        )
    except CosmosResourceNotFoundError:
        return _failure("Approval was not found")

    if grant.get("status") == "consumed":
        return {
            "success": True,
            "duplicate": True,
            "request_id": str(grant.get("request_id") or ""),
            "submitted_at": str(grant.get("consumed_at") or ""),
            "message": "この申請は登録済みです。",
        }
    if grant.get("status") != "awaiting_confirmation":
        return _failure("Approval is not awaiting confirmation")
    expires_at = datetime.fromisoformat(
        str(grant["expires_at"]).replace("Z", "+00:00")
    )
    if expires_at <= datetime.now(timezone.utc):
        return _failure("Approval has expired")

    idempotency_key = str(grant["idempotency_key"])
    request_id = (
        f"TR-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:12].upper()}"
    )
    requests = get_container()
    try:
        existing = await requests.read_item(
            item=request_id,
            partition_key=request_id,
        )
    except CosmosResourceNotFoundError:
        existing = None
    if existing:
        return {
            "success": True,
            "duplicate": True,
            "request_id": request_id,
            "submitted_at": existing["submitted_at"],
            "message": f"出張申請 {request_id} は登録済みです。",
        }

    submitted_at = datetime.now(timezone.utc).isoformat()
    conversation_id = str(grant.get("conversation_id") or "")
    document = {
        "id": request_id,
        "request_id": request_id,
        "user_id": grant["user_id"],
        "conversation_id": conversation_id or f"foundry-{approval_id}",
        "approval_grant_id": approval_id,
        "idempotency_key": idempotency_key,
        "plan_hash": grant["plan_hash"],
        "approval_mode": "prompt_agent_mcp",
        "status": "submitted",
        "submitted_at": submitted_at,
        "application_text": grant["application_text"],
        "policy_result": grant["policy_result"],
        **grant["application_data"],
    }
    try:
        await requests.create_item(document, if_none_match="*")
    except CosmosHttpResponseError as exc:
        if exc.status_code != 409:
            raise

    grant["status"] = "consumed"
    grant["consumed_at"] = submitted_at
    grant["request_id"] = request_id
    try:
        await grants.replace_item(
            item=approval_id,
            body=grant,
            etag=grant.get("_etag"),
            match_condition=MatchConditions.IfNotModified,
        )
    except CosmosHttpResponseError as exc:
        if exc.status_code != 412:
            raise
        current = await grants.read_item(
            item=approval_id,
            partition_key=approval_id,
        )
        if current.get("request_id") != request_id:
            raise

    logger.info(
        "Travel request %s stored for user %s through Prompt Agent MCP",
        request_id,
        grant["user_id"],
    )
    return {
        "success": True,
        "duplicate": False,
        "request_id": request_id,
        "submitted_at": submitted_at,
        "message": f"出張申請 {request_id} を登録しました。",
    }
