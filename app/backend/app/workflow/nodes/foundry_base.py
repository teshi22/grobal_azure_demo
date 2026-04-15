"""Foundry Agent Executor 基底クラス

Foundry Agent Service (Responses API) を Agent Framework の
Executor としてラップする共通基盤。
"""

import asyncio
import json
import logging
import time
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
        t0 = time.perf_counter()
        openai_client = get_openai_client()
        t1 = time.perf_counter()
        func_result = None

        conv = openai_client.conversations.create(
            items=[{"type": "message", "role": "user", "content": input_text}],
        )
        t2 = time.perf_counter()

        response = openai_client.responses.create(
            conversation=conv.id,
            extra_body={
                "agent_reference": {
                    "name": self.agent_name,
                    "type": "agent_reference",
                }
            },
        )
        t3 = time.perf_counter()

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

        # conversation 削除はバックグラウンドで実行 (待ちを排除)
        conv_id_to_delete = conv.id
        def _cleanup():
            try:
                openai_client.conversations.delete(conversation_id=conv_id_to_delete)
            except Exception:
                pass
        import threading
        threading.Thread(target=_cleanup, daemon=True).start()

        t4 = time.perf_counter()
        logger.info(
            "[PERF] _call_agent(%s): client=%.3fs, conv_create=%.3fs, "
            "responses=%.3fs, total=%.3fs",
            self.agent_name, t1 - t0, t2 - t1, t3 - t2, t4 - t0,
        )

        return text, func_result
