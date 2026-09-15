"""API リクエスト/レスポンス スキーマ"""

from pydantic import BaseModel, Field


class CreateConversationRequest(BaseModel):
    """Conversation identity is derived from the authenticated user."""


class ConversationResponse(BaseModel):
    conversation_id: str
    status: str


class SendMessageRequest(BaseModel):
    content: str = Field(description="メッセージ内容")


class MessageResponse(BaseModel):
    message_id: str
    status: str  # accepted, duplicate, error
