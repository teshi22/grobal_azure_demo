"""Authenticated client for the travel request MCP Function."""

from __future__ import annotations

import json
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .settings import settings

_credential: DefaultAzureCredential | None = None


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


async def _get_headers() -> dict[str, str]:
    if not settings.mcp_function_app_client_id:
        return {}
    token = await _get_credential().get_token(
        f"api://{settings.mcp_function_app_client_id}/.default"
    )
    return {"Authorization": f"Bearer {token.token}"}


async def _call_tool(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if not settings.mcp_tool_endpoint:
        raise RuntimeError("MCP_TOOL_ENDPOINT is not configured")

    async with streamablehttp_client(
        settings.mcp_tool_endpoint,
        headers=await _get_headers(),
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                tool_name,
                arguments=arguments,
            )

    if result.isError:
        message = result.content[0].text if result.content else "Unknown MCP error"
        raise RuntimeError(message)
    if not result.content:
        raise RuntimeError("MCP tool returned no content")

    payload = json.loads(result.content[0].text)
    if not payload.get("success"):
        raise RuntimeError(str(payload.get("message", "Submission failed")))
    return payload


async def prepare_travel_request_submission(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return await _call_tool(
        "prepare_travel_request_submission",
        arguments,
    )


async def submit_travel_request_with_approval(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return await _call_tool(
        "submit_travel_request_with_approval",
        arguments,
    )


async def submit_travel_request(arguments: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for older workflow versions."""
    return await _call_tool("submit_travel_request", arguments)
