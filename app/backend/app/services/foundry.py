"""Foundry Agent Service クライアント

AIProjectClient + OpenAI クライアントを初期化し、
ワークフローノードから Foundry Agent を呼び出すためのユーティリティを提供する。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from app.config import settings

logger = logging.getLogger(__name__)

_project_client: AIProjectClient | None = None
_credential: DefaultAzureCredential | None = None
_agent_responses: dict[str, Any] = {}


def get_project_client() -> AIProjectClient:
    global _project_client, _credential
    if _project_client is None:
        _credential = DefaultAzureCredential()
        _project_client = AIProjectClient(
            endpoint=settings.azure_ai_project_endpoint,
            credential=_credential,
            allow_preview=True,
        )
        logger.info("AIProjectClient initialized")
    return _project_client


def get_agent_responses_client(agent_name: str):
    """Return a Responses client bound to an active Foundry Agent."""
    if agent_name not in _agent_responses:
        _agent_responses[agent_name] = get_project_client().get_openai_client(
            agent_name=agent_name,
        ).responses
        logger.info("Foundry Agent Responses client initialized: %s", agent_name)
    return _agent_responses[agent_name]


def get_hosted_responses_client():
    """Return a Responses client bound to the deployed Hosted Agent."""
    return get_agent_responses_client(settings.hosted_agent_name)


def invoke_conversation_agent(
    *,
    agent_name: str,
    message_envelope: dict[str, Any],
    previous_response_id: str | None = None,
    agent_session_id: str | None = None,
    user_identity: str | None = None,
):
    """Start or resume a durable Foundry Agent chat response."""
    kwargs: dict[str, Any] = {
        "input": json.dumps(message_envelope, ensure_ascii=False),
        "store": True,
        "stream": False,
    }
    if agent_session_id:
        kwargs["extra_body"] = {"agent_session_id": agent_session_id}
    if user_identity:
        kwargs["extra_headers"] = {"x-ms-user-identity": user_identity}
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    responses = (
        get_hosted_responses_client()
        if agent_name == settings.hosted_agent_name
        else get_agent_responses_client(agent_name)
    )
    return responses.create(**kwargs)


def invoke_hosted_agent(
    *,
    conversation_id: str,
    user_id: str,
    message: str,
    previous_response_id: str | None = None,
):
    """Start or resume a Hosted Agent with an ordinary chat turn."""
    return invoke_conversation_agent(
        agent_name=settings.hosted_agent_name,
        message_envelope={
            "conversation_id": conversation_id,
            "message": message,
        },
        previous_response_id=previous_response_id,
        agent_session_id=conversation_id,
        user_identity=user_id,
    )


def invoke_single_prompt_agent(
    *,
    conversation_id: str,
    message: str,
    previous_response_id: str | None = None,
):
    """Start or resume an interactive Single Prompt Agent conversation."""
    return invoke_conversation_agent(
        agent_name=settings.single_prompt_agent_name,
        message_envelope={
            "conversation_id": conversation_id,
            "input": message,
        },
        previous_response_id=previous_response_id,
    )


def invoke_scenario_agent(
    *,
    agent_name: str,
    session_id: str | None,
    envelope: dict[str, Any],
):
    """Run one active Foundry Agent without storing state or side effects."""
    kwargs: dict[str, Any] = {
        "input": json.dumps(envelope, ensure_ascii=False),
        "store": False,
        "stream": False,
    }
    if session_id:
        kwargs["extra_body"] = {"agent_session_id": session_id}
    return get_agent_responses_client(agent_name).create(**kwargs)


def response_to_dict(response: Any) -> dict[str, Any]:
    """Convert the OpenAI response model to JSON-compatible data."""
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=True)
    if hasattr(response, "to_dict"):
        return response.to_dict()
    raise TypeError(f"Unsupported response type: {type(response)!r}")


def close_foundry_client() -> None:
    global _project_client, _credential
    _agent_responses.clear()
    if _project_client is not None:
        _project_client.close()
        _project_client = None
    if _credential is not None:
        _credential.close()
        _credential = None
