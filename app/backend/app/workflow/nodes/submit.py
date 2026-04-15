"""出張申請登録ノード — MCP ツールで決定論的に申請を送信"""

import json
import logging

from agent_framework import Executor, handler

from app.services.mcp_client import call_submit_tool

logger = logging.getLogger(__name__)


class SubmitTravelRequestStep(Executor):
    """ApprovalAgent の出力を受け取り、MCP ツールで申請登録する。

    LLM ではなくワークフローノードから決定論的に呼び出すことで、
    二重送信やリトライ時の副作用を防止する。
    """

    def __init__(self):
        super().__init__(id="submit_travel_request")

    @handler(input=str, output=str)
    async def run(self, approval_text, ctx) -> None:
        logger.info("Submitting travel request via MCP tool")
        result = await call_submit_tool({"application_text": approval_text})

        if result.get("status") == "submitted":
            output = f"{approval_text}\n\n✅ 出張申請を申請システムへ送信しました！"
        elif result.get("status") == "skipped":
            output = f"{approval_text}\n\n⚠️ 申請システム未設定のため送信をスキップしました。"
        else:
            output = f"{approval_text}\n\n❌ 申請送信に失敗しました: {result}"

        await ctx.yield_output(output)
