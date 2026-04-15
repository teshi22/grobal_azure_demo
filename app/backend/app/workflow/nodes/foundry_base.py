"""Foundry Agent Executor 基底クラス

Foundry Agent Service (Responses API) を Agent Framework の
Executor としてラップする共通基盤。
"""

import asyncio
import json
import logging
from typing import Any

from agent_framework import Executor, handler

from app.services.foundry import get_openai_client

logger = logging.getLogger(__name__)


class FoundryAgentNode(Executor):
    """Foundry Agent を Agent Framework ワークフローノードとして使用する。

    - Responses API で呼び出し
    - FunctionTool の function_call を自動ハンドリング
    - 結果を shared state に保存
    """

    def __init__(
        self,
        id: str,
        agent_name: str,
        function_handler: Any | None = None,
    ):
        super().__init__(id=id)
        self.agent_name = agent_name
        self.function_handler = function_handler

    @handler(input=str, output=str)
    async def invoke(self, input_text, ctx) -> None:
        text, func_result = await asyncio.to_thread(self._call_agent, input_text)
        ctx.set_state(f"{self.id}_response", text)
        if func_result is not None:
            ctx.set_state(f"{self.id}_function_result", func_result)
        await ctx.send_message(text)

    def _call_agent(self, input_text: str) -> tuple[str, dict | None]:
        openai_client = get_openai_client()
        func_result = None

        conv = openai_client.conversations.create(
            items=[{"type": "message", "role": "user", "content": input_text}],
        )

        response = openai_client.responses.create(
            conversation=conv.id,
            extra_body={
                "agent_reference": {
                    "name": self.agent_name,
                    "type": "agent_reference",
                }
            },
        )

        # FunctionTool の function_call をハンドリング
        if self.function_handler:
            for output in response.output:
                if output.type == "function_call":
                    args = json.loads(output.arguments)
                    func_result = self.function_handler(args)
                    openai_client.conversations.items.create(
                        conversation_id=conv.id,
                        items=[
                            {
                                "type": "function_call_output",
                                "call_id": output.call_id,
                                "output": json.dumps(
                                    func_result, ensure_ascii=False
                                ),
                            }
                        ],
                    )
                    response = openai_client.responses.create(
                        conversation=conv.id,
                        extra_body={
                            "agent_reference": {
                                "name": self.agent_name,
                                "type": "agent_reference",
                            }
                        },
                    )

        text = response.output_text

        try:
            openai_client.conversations.delete(conversation_id=conv.id)
        except Exception:
            pass

        return text, func_result
