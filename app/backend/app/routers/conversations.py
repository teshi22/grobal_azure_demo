"""会話 API エンドポイント

POST /api/conversations          — 新規会話作成
POST /api/conversations/{id}/messages — メッセージ送信 (初回 + HITL 応答)
GET  /api/conversations/{id}     — 会話情報取得
"""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.schemas.api import (
    ConversationResponse,
    CreateConversationRequest,
    MessageResponse,
    SendMessageRequest,
)
from app.services.cosmos import get_conversation_store, get_event_store
from app.workflow.runner import run_workflow_async, resume_workflow_async

router = APIRouter(tags=["conversations"])
logger = logging.getLogger(__name__)


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(req: CreateConversationRequest):
    """新規会話を作成する"""
    conversation_id = str(uuid.uuid4())
    store = get_conversation_store()
    await store.create(
        conversation_id=conversation_id,
        user_id=req.user_id,
    )
    return ConversationResponse(
        conversation_id=conversation_id,
        status="created",
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: str):
    """会話情報を取得する"""
    store = get_conversation_store()
    conv = await store.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationResponse(
        conversation_id=conv["id"],
        status=conv.get("status", "unknown"),
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
)
async def send_message(
    conversation_id: str,
    req: SendMessageRequest,
    background_tasks: BackgroundTasks,
    x_idempotency_key: str | None = Header(None),
):
    """メッセージを送信する (初回リクエスト or HITL 応答)

    冪等性キーで重複送信を防止する。
    """
    store = get_conversation_store()
    conv = await store.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 冪等性チェック
    if x_idempotency_key:
        event_store = get_event_store()
        existing = await event_store.get_by_idempotency_key(
            conversation_id, x_idempotency_key
        )
        if existing:
            return MessageResponse(
                message_id=existing["message_id"],
                status="duplicate",
            )

    message_id = str(uuid.uuid4())
    status = conv.get("status", "created")

    if status in ("created", "completed", "error"):
        # 初回メッセージ → ワークフロー開始
        background_tasks.add_task(
            run_workflow_async,
            conversation_id=conversation_id,
            user_input=req.content,
            message_id=message_id,
        )
    elif status == "waiting_for_input":
        # HITL 応答 → ワークフロー再開
        background_tasks.add_task(
            resume_workflow_async,
            conversation_id=conversation_id,
            user_input=req.content,
            message_id=message_id,
        )
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Conversation is in '{status}' state, cannot accept messages",
        )

    await store.update_status(conversation_id, "processing")

    return MessageResponse(message_id=message_id, status="accepted")
