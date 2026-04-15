"""SSE イベントスキーマ"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SSEEventType(str, Enum):
    STATUS = "status"
    AGENT_RESPONSE = "agent_response"
    HITL_REQUEST = "hitl_request"
    COMPLETE = "complete"
    ERROR = "error"


class StatusEvent(BaseModel):
    """ワークフロー進行状況"""
    step: str
    label: str


class AgentResponseEvent(BaseModel):
    """エージェント応答"""
    step: str
    content: str


class HITLRequestEvent(BaseModel):
    """HITL リクエスト"""
    type: str  # "plan_review" | "clarification"
    data: dict[str, Any]
    message: str


class CompleteEvent(BaseModel):
    """ワークフロー完了"""
    output: str


class ErrorEvent(BaseModel):
    """エラー"""
    message: str
    step: str | None = None


def format_sse_event(event_type: SSEEventType, payload: BaseModel) -> str:
    """SSE イベントをフォーマットする"""
    return json.dumps(
        {"event_type": event_type.value, **payload.model_dump()},
        ensure_ascii=False,
    )
