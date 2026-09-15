"""Azure Functions + MCP Server — 出張申請登録ツール

Streamable HTTP トランスポートで MCP ツールを提供する。
"""

import json
import logging

import azure.functions as func
from tools.submit_travel_request import submit_travel_request

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

logger = logging.getLogger(__name__)

# MCP ツール定義
MCP_TOOLS = [
    {
        "name": "submit_travel_request",
        "description": "出張申請書を申請システムに登録する",
        "inputSchema": {
            "type": "object",
            "properties": {
                "application_text": {
                    "type": "string",
                    "description": "出張申請書のテキスト",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "BFF が所有権を確認した会話 ID",
                },
                "approval_grant_id": {
                    "type": "string",
                    "description": "BFF が発行した短命・一度限りの承認 grant",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "申請 ID を決定する冪等性キー",
                },
                "plan_hash": {
                    "type": "string",
                    "description": "承認済み旅程の SHA-256",
                },
                "application_data": {
                    "type": "object",
                    "description": "構造化された出張申請データ",
                },
            },
            "required": [
                "application_text",
                "conversation_id",
                "approval_grant_id",
                "idempotency_key",
                "plan_hash",
                "application_data",
            ],
        },
    },
]


@app.route(route="mcp", methods=["POST"])
async def mcp_handler(req: func.HttpRequest) -> func.HttpResponse:
    """MCP Streamable HTTP エンドポイント

    JSON-RPC リクエストを受け付け、MCP プロトコルに従って応答する。
    """
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}),
            status_code=400,
            mimetype="application/json",
        )

    method = body.get("method", "")
    request_id = body.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "travel-mcp-tools", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": MCP_TOOLS}
    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "submit_travel_request":
            tool_result = await submit_travel_request(arguments)
            result = {
                "content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}],
                "isError": not tool_result.get("success", False),
            }
        else:
            result = {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }
    elif method == "notifications/initialized":
        return func.HttpResponse(status_code=204)
    else:
        return func.HttpResponse(
            json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }),
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False),
        mimetype="application/json",
    )
