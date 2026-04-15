"""Azure Functions + MCP Server — 出張申請登録ツール

Streamable HTTP トランスポートで MCP ツールを提供する。
"""

import json
import logging

import azure.functions as func
from tools.submit_travel_request import submit_travel_request

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

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
            },
            "required": ["application_text"],
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
