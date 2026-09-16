"""API リクエスト/レスポンス スキーマ"""

from typing import Literal

from pydantic import BaseModel, Field

ConversationScenario = Literal[
    "agent_framework_workflow",
    "single_prompt_agent",
]


class CreateConversationRequest(BaseModel):
    """Conversation identity is derived from the authenticated user."""

    scenario: ConversationScenario = "agent_framework_workflow"


class ConversationResponse(BaseModel):
    conversation_id: str
    status: str


class SendMessageRequest(BaseModel):
    content: str = Field(description="メッセージ内容")


class MessageResponse(BaseModel):
    message_id: str
    status: str  # accepted, duplicate, error
