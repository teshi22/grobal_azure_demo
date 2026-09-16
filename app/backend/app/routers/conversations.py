"""Authenticated conversation and durable SSE endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.auth.entra import CurrentUser
from app.schemas.api import (
    ConversationResponse,
    CreateConversationRequest,
    MessageResponse,
    SendMessageRequest,
)
from app.services.cosmos import get_conversation_store, get_event_store
from app.services.hosted_agent import process_queued_message

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _interactive_mode(requested_mode: str) -> str:
    # The former playground route is now the same MCP-backed application flow.
    return "submission" if requested_mode == "playground" else requested_mode


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    request: CreateConversationRequest,
    current_user: CurrentUser,
):
    conversation_id = str(uuid.uuid4())
    await get_conversation_store().create(
        conversation_id,
        current_user["sub"],
        scenario=request.scenario,
        interaction_mode=_interactive_mode(request.interaction_mode),
    )
    return ConversationResponse(
        conversation_id=conversation_id,
        status="created",
    )


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    conversations = get_conversation_store()
    events = get_event_store()
    conversation = await conversations.get_owned(
        conversation_id,
        current_user["sub"],
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    existing = await events.get_by_idempotency_key(
        conversation_id,
        idempotency_key,
    )
    if existing:
        return MessageResponse(message_id=existing["message_id"], status="duplicate")

    message_id = str(uuid.uuid4())
    if not await conversations.claim_message(
        conversation_id,
        idempotency_key,
        message_id=message_id,
        user_id=current_user["sub"],
        content=body.content,
    ):
        latest = await conversations.get(conversation_id)
        if latest and latest.get("last_idempotency_key") == idempotency_key:
            active_message = latest.get("active_message") or {}
            return MessageResponse(
                message_id=str(active_message.get("message_id", "")),
                status="duplicate",
            )
        if latest and latest.get("status") == "processing":
            raise HTTPException(
                status_code=409,
                detail="Conversation is already processing",
            )
        return MessageResponse(message_id="", status="duplicate")

    await events.append(
        conversation_id,
        "user_message",
        {"content": body.content},
        message_id=message_id,
        idempotency_key=idempotency_key,
    )
    await events.append(
        conversation_id,
        "status",
        {"step": "hosted_agent", "label": "処理中..."},
        message_id=message_id,
    )
    background_tasks.add_task(
        process_queued_message,
        conversation_id=conversation_id,
    )
    return MessageResponse(message_id=message_id, status="accepted")
