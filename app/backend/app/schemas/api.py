"""API リクエスト/レスポンス スキーマ"""

from pydantic import BaseModel, Field


class CreateConversationRequest(BaseModel):
    user_id: str = Field(description="ユーザー ID")


class ConversationResponse(BaseModel):
    conversation_id: str
    status: str


class SendMessageRequest(BaseModel):
    content: str = Field(description="メッセージ内容")


class MessageResponse(BaseModel):
    message_id: str
    status: str  # accepted, duplicate, error
