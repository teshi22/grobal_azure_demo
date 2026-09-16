"""API リクエスト/レスポンス スキーマ"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ConversationScenario = Literal[
    "agent_framework_workflow",
    "single_prompt_agent",
]
ConversationInteractionMode = Literal["submission", "playground"]


class CreateConversationRequest(BaseModel):
    """Conversation identity is derived from the authenticated user."""

    scenario: ConversationScenario = "agent_framework_workflow"
    interaction_mode: ConversationInteractionMode = "submission"

    @model_validator(mode="after")
    def validate_scenario_mode(self):
        if (
            self.scenario == "single_prompt_agent"
            and self.interaction_mode == "submission"
        ):
            raise ValueError(
                "single_prompt_agent does not support submission mode"
            )
        return self


class ConversationResponse(BaseModel):
    conversation_id: str
    status: str


class SendMessageRequest(BaseModel):
    content: str = Field(description="メッセージ内容")


class MessageResponse(BaseModel):
    message_id: str
    status: str  # accepted, duplicate, error
