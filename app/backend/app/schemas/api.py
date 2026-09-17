"""API リクエスト/レスポンス スキーマ"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    content: str | None = Field(default=None, description="メッセージ内容")
    approval_request_id: str | None = None
    approve: bool | None = None

    @model_validator(mode="after")
    def validate_input(self) -> "SendMessageRequest":
        has_message = isinstance(self.content, str) and bool(
            self.content.strip()
        )
        has_approval = (
            isinstance(self.approval_request_id, str)
            and bool(self.approval_request_id.strip())
            and isinstance(self.approve, bool)
        )
        if has_message == has_approval:
            raise ValueError("Provide exactly one message or MCP approval")
        if has_message:
            self.content = self.content.strip()
        return self

    @property
    def display_content(self) -> str:
        if self.content is not None:
            return self.content
        return (
            "MCPツールの実行を承認しました"
            if self.approve
            else "MCPツールの実行を拒否しました"
        )


class MessageResponse(BaseModel):
    message_id: str
    status: str  # accepted, duplicate, error
