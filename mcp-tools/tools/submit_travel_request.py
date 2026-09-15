"""Validate a one-time approval grant and persist one travel request."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from tools.cosmos_client import get_approval_grant_container, get_container

logger = logging.getLogger(__name__)


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
