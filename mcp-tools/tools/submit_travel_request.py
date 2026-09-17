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
_HOSTED_AGENT_FALLBACK_USER = "foundry-hosted-agent"
_EXPLICIT_APPROVALS = frozenset(
    {
        "ok",
        "yes",
        "y",
        "はい",
        "承認",
        "承認します",
        "申請",
        "申請する",
        "申請します",
        "申請してください",
    }
)
_APPLICATION_STRING_FIELDS = (
    "departure",
    "destination",
    "purpose",
    "schedule",
    "fare_basis",
    "searched_at",
)
_LEG_STRING_FIELDS = (
    "direction",
    "method",
    "from",
    "to",
    "fare_type",
    "source_url",
    "source_title",
)

TRANSPORTATION_LEG_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string"},
        "method": {"type": "string"},
        "from": {"type": "string"},
        "to": {"type": "string"},
        "cost": {"type": "integer", "minimum": 0},
        "fare_type": {"type": "string"},
        "source_url": {"type": "string"},
        "source_title": {"type": "string"},
    },
    "required": [
        "direction",
        "method",
        "from",
        "to",
        "cost",
        "fare_type",
        "source_url",
        "source_title",
    ],
    "additionalProperties": False,
}

APPLICATION_DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "departure": {"type": "string"},
        "destination": {"type": "string"},
        "purpose": {"type": "string"},
        "schedule": {"type": "string"},
        "trip_type": {"type": "string", "enum": ["日帰り", "宿泊"]},
        "transportation_legs": {
            "type": "array",
            "items": TRANSPORTATION_LEG_SCHEMA,
            "minItems": 2,
        },
        "transportation_cost": {"type": "integer", "minimum": 0},
        "hotel": {"type": ["string", "null"]},
        "hotel_cost_per_night": {
            "type": ["integer", "null"],
            "minimum": 0,
        },
        "hotel_nights": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "total_cost": {"type": "integer", "minimum": 0},
        "distance_km": {"type": "number", "minimum": 0},
        "travel_time_hours": {"type": "number", "minimum": 0},
        "fare_basis": {"type": "string"},
        "searched_at": {"type": "string"},
    },
    "required": [
        "departure",
        "destination",
        "purpose",
        "schedule",
        "trip_type",
        "transportation_legs",
        "transportation_cost",
        "hotel",
        "hotel_cost_per_night",
        "hotel_nights",
        "total_cost",
        "distance_km",
        "travel_time_hours",
        "fare_basis",
        "searched_at",
    ],
    "additionalProperties": False,
}


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
    application_error = _validate_application_data(arguments["application_data"])
    if application_error:
        return application_error
    conversation_id = arguments.get("conversation_id")
    if conversation_id is not None and (
        not isinstance(conversation_id, str) or not conversation_id.strip()
    ):
        return "conversation_id must be a non-empty string when provided"
    agent_scenario = arguments.get("agent_scenario")
    if agent_scenario is not None and agent_scenario not in {
        "single_prompt_agent",
        "agent_framework_workflow",
    }:
        return "agent_scenario is not supported"
    return None


def _validate_application_data(application_data: dict) -> str | None:
    for key in _APPLICATION_STRING_FIELDS:
        if (
            not isinstance(application_data.get(key), str)
            or not application_data[key].strip()
        ):
            return f"application_data.{key} is required"

    trip_type = application_data.get("trip_type")
    if trip_type not in {"日帰り", "宿泊"}:
        return "application_data.trip_type must be 日帰り or 宿泊"

    legs = application_data.get("transportation_legs")
    if not isinstance(legs, list) or len(legs) < 2:
        return "application_data.transportation_legs must contain outbound and return legs"
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            return f"application_data.transportation_legs[{index}] must be an object"
        for key in _LEG_STRING_FIELDS:
            if not isinstance(leg.get(key), str) or not leg[key].strip():
                return (
                    f"application_data.transportation_legs[{index}].{key} "
                    "is required"
                )
        cost = leg.get("cost")
        if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
            return (
                f"application_data.transportation_legs[{index}].cost "
                "must be a non-negative integer"
            )

    transportation_cost = application_data.get("transportation_cost")
    if (
        not isinstance(transportation_cost, int)
        or isinstance(transportation_cost, bool)
        or transportation_cost < 0
    ):
        return "application_data.transportation_cost must be a non-negative integer"
    if transportation_cost != sum(leg["cost"] for leg in legs):
        return "application_data.transportation_cost does not match leg costs"

    for key in ("distance_km", "travel_time_hours"):
        value = application_data.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < 0
        ):
            return f"application_data.{key} must be a non-negative number"

    if trip_type == "日帰り":
        if any(
            application_data.get(key) is not None
            for key in ("hotel", "hotel_cost_per_night", "hotel_nights")
        ):
            return "Day trips must not contain hotel details"
        hotel_cost = 0
    else:
        hotel = application_data.get("hotel")
        hotel_cost_per_night = application_data.get("hotel_cost_per_night")
        hotel_nights = application_data.get("hotel_nights")
        if not isinstance(hotel, str) or not hotel.strip():
            return "application_data.hotel is required for overnight trips"
        if (
            not isinstance(hotel_cost_per_night, int)
            or isinstance(hotel_cost_per_night, bool)
            or hotel_cost_per_night < 0
        ):
            return "application_data.hotel_cost_per_night must be a non-negative integer"
        if (
            not isinstance(hotel_nights, int)
            or isinstance(hotel_nights, bool)
            or hotel_nights < 1
        ):
            return "application_data.hotel_nights must be a positive integer"
        hotel_cost = hotel_cost_per_night * hotel_nights

    total_cost = application_data.get("total_cost")
    if (
        not isinstance(total_cost, int)
        or isinstance(total_cost, bool)
        or total_cost != transportation_cost + hotel_cost
    ):
        return "application_data.total_cost does not match travel costs"
    return None


def _validate_direct_arguments(arguments: dict) -> str | None:
    if (
        not isinstance(arguments.get("approval_id"), str)
        or not arguments["approval_id"].strip()
    ):
        return "approval_id is required"
    confirmation_text = arguments.get("confirmation_text")
    if (
        not isinstance(confirmation_text, str)
        or not confirmation_text.strip()
    ):
        return "confirmation_text is required"
    return None


def _is_explicit_approval(confirmation_text: str) -> bool:
    normalized = confirmation_text.strip().lower().rstrip("。.!！")
    return normalized in _EXPLICIT_APPROVALS


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
    approval_mode = "prompt_agent_mcp"
    if arguments.get("agent_scenario") == "agent_framework_workflow":
        user_id = _HOSTED_AGENT_FALLBACK_USER
        approval_mode = "hosted_agent_mcp"
    if conversation_id:
        conversations = get_conversation_container()
        try:
            conversation = await conversations.read_item(
                item=conversation_id,
                partition_key=conversation_id,
            )
        except CosmosResourceNotFoundError:
            return _failure("Conversation was not found")

        scenario = str(conversation.get("scenario") or "")
        if scenario not in {
            "single_prompt_agent",
            "agent_framework_workflow",
        }:
            return _failure("Conversation scenario does not support MCP approval")
        user_id = str(conversation.get("user_id") or "").strip()
        if not user_id:
            return _failure("Conversation has no owner")
        if scenario == "agent_framework_workflow":
            approval_mode = "hosted_agent_mcp"

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
            f"{approval_mode}:{approval_id}".encode("utf-8")
        ).hexdigest(),
        "approval_mode": approval_mode,
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
            "submitted": True,
            "cancelled": False,
            "duplicate": True,
            "request_id": str(grant.get("request_id") or ""),
            "submitted_at": str(grant.get("consumed_at") or ""),
            "message": "この申請は登録済みです。",
        }
    if grant.get("status") == "cancelled":
        return {
            "success": True,
            "submitted": False,
            "cancelled": True,
            "duplicate": True,
            "request_id": "",
            "submitted_at": "",
            "message": "出張申請の送信をキャンセルしました。",
        }
    if grant.get("status") != "awaiting_confirmation":
        return _failure("Approval is not awaiting confirmation")
    expires_at = datetime.fromisoformat(
        str(grant["expires_at"]).replace("Z", "+00:00")
    )
    if expires_at <= datetime.now(timezone.utc):
        return _failure("Approval has expired")

    if not _is_explicit_approval(arguments["confirmation_text"]):
        cancelled_at = datetime.now(timezone.utc).isoformat()
        grant["status"] = "cancelled"
        grant["cancelled_at"] = cancelled_at
        grant["confirmation_text"] = arguments["confirmation_text"]
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
            if current.get("status") != "cancelled":
                return _failure("Approval state changed before cancellation")
        return {
            "success": True,
            "submitted": False,
            "cancelled": True,
            "duplicate": False,
            "request_id": "",
            "submitted_at": "",
            "message": "出張申請の送信をキャンセルしました。",
        }

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
        "approval_mode": grant.get("approval_mode", "prompt_agent_mcp"),
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
        "submitted": True,
        "cancelled": False,
        "duplicate": False,
        "request_id": request_id,
        "submitted_at": submitted_at,
        "message": f"出張申請 {request_id} を登録しました。",
    }
