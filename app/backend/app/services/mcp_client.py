"""MCP クライアント — 出張申請登録ツール呼び出し

Streamable HTTP トランスポートで Azure Functions 上の MCP サーバーに接続し、
submit_travel_request ツールを呼び出す。
EasyAuth (Entra ID) 認証にはマネージド ID のBearerトークンを使用する。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings

logger = logging.getLogger(__name__)

# マネージド ID 用の共有 credential インスタンス（モジュールロード時に生成）
_credential: DefaultAzureCredential | None = None


def _get_credential() -> DefaultAzureCredential:
    """DefaultAzureCredential のシングルトンを返す"""
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


async def _get_auth_headers() -> dict[str, str]:
    """MCP Functions 呼び出し用の Bearer トークンヘッダーを取得する"""
    client_id = settings.mcp_function_app_client_id
    if not client_id:
        return {}
    scope = f"api://{client_id}/.default"
    credential = _get_credential()
    token = await credential.get_token(scope)
    return {"Authorization": f"Bearer {token.token}"}


async def call_submit_tool(
    application_data: dict[str, Any],
    conversation_id: str = "",
) -> dict[str, Any]:
    """出張申請登録 MCP ツールを呼び出す

    Args:
        application_data: 申請書データ
        conversation_id: 会話 ID（べき等性キー）

    Returns:
        ツール実行結果
    """
    endpoint = settings.mcp_tool_endpoint
    if not endpoint:
        logger.warning("MCP tool endpoint not configured, skipping submission")
        return {"status": "skipped", "message": "MCP endpoint not configured"}

    arguments = {**application_data}
    if conversation_id:
        arguments["conversation_id"] = conversation_id

    headers = await _get_auth_headers()

    async with streamablehttp_client(endpoint, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "submit_travel_request",
                arguments=arguments,
            )
            logger.info("MCP tool result: %s", result)

            # MCP レスポンスからツール結果を解析
            if result.isError:
                error_text = result.content[0].text if result.content else "Unknown error"
                return {"status": "error", "message": error_text}

            try:
                tool_result = json.loads(result.content[0].text)
                if tool_result.get("success"):
                    return {
                        "status": "submitted",
                        "request_id": tool_result.get("request_id", ""),
                        "message": tool_result.get("message", ""),
                    }
                return {"status": "error", "message": tool_result.get("message", "")}
            except (json.JSONDecodeError, IndexError, AttributeError):
                return {"status": "submitted", "result": str(result)}


def call_submit_tool_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Foundry Agent の function_handler 用同期ラッパー

    FoundryAgentNode._call_agent() は asyncio.to_thread() 内で実行されるため、
    新しいイベントループで非同期 MCP 呼び出しを実行する。
    """
    return asyncio.run(call_submit_tool(args))
