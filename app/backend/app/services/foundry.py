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
_hosted_responses = None


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


def get_hosted_responses_client():
    """Return a Responses client bound to the deployed Hosted Agent."""
    global _hosted_responses
    if _hosted_responses is None:
        _hosted_responses = get_project_client().get_openai_client(
            agent_name=settings.hosted_agent_name,
        ).responses
        logger.info("Hosted Agent Responses client initialized")
    return _hosted_responses


def invoke_hosted_agent(
    *,
    conversation_id: str,
    user_id: str,
    message: str | None = None,
    previous_response_id: str | None = None,
    function_call_id: str | None = None,
    function_output: dict[str, Any] | None = None,
):
    """Start or resume a Hosted Agent response for one authenticated user."""
    if (message is None) == (function_output is None):
        raise ValueError("Provide either message or function_output")

    if function_output is not None:
        if not function_call_id:
            raise ValueError("function_call_id is required for function output")
        response_input: str | list[dict[str, str]] = [
            {
                "type": "function_call_output",
                "call_id": function_call_id,
                "output": json.dumps(function_output, ensure_ascii=False),
            }
        ]
    else:
        response_input = json.dumps(
            {
                "conversation_id": conversation_id,
                "message": message,
            },
            ensure_ascii=False,
        )

    kwargs: dict[str, Any] = {
        "input": response_input,
        "store": True,
        "stream": False,
        "extra_body": {"agent_session_id": conversation_id},
        "extra_headers": {"x-ms-user-identity": user_id},
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    return get_hosted_responses_client().create(**kwargs)


def response_to_dict(response: Any) -> dict[str, Any]:
    """Convert the OpenAI response model to JSON-compatible data."""
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=True)
    if hasattr(response, "to_dict"):
        return response.to_dict()
    raise TypeError(f"Unsupported response type: {type(response)!r}")


def close_foundry_client() -> None:
    global _project_client, _credential, _hosted_responses
    _hosted_responses = None
    if _project_client is not None:
        _project_client.close()
        _project_client = None
    if _credential is not None:
        _credential.close()
        _credential = None
