"""MCP クライアント — 出張申請登録ツール呼び出し

Streamable HTTP トランスポートで Azure Functions 上の MCP サーバーに接続し、
submit_travel_request ツールを呼び出す。
"""

from __future__ import annotations

import logging
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings

logger = logging.getLogger(__name__)


async def call_submit_tool(application_data: dict[str, Any]) -> dict[str, Any]:
    """出張申請登録 MCP ツールを呼び出す

    Args:
        application_data: 申請書データ

    Returns:
        ツール実行結果
    """
    endpoint = settings.mcp_tool_endpoint
    if not endpoint:
        logger.warning("MCP tool endpoint not configured, skipping submission")
        return {"status": "skipped", "message": "MCP endpoint not configured"}

    async with streamablehttp_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "submit_travel_request",
                arguments=application_data,
            )
            logger.info(f"MCP tool result: {result}")
            return {"status": "submitted", "result": str(result)}
