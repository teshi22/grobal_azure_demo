"""RequestClarifier ノード — 入力情報の過不足を判定し、不足時に HITL で質問"""

import asyncio
import json
import logging
import re
from typing import Any

from agent_framework import Executor, WorkflowContext, handler, response_handler

from app.config import settings
from app.services.foundry import get_openai_client
from app.workflow.models import (
    ClarificationHITLRequest,
    ClarificationHITLResponse,
    ClarificationResult,
    RequestConfirmHITLRequest,
    RequestConfirmHITLResponse,
)

logger = logging.getLogger(__name__)


class RequestClarifierStep(Executor):
    """Step 0: RequestClarifier Foundry Agent で入力の過不足を判定"""

    def __init__(self):
        super().__init__(id="request_clarifier")

    def _call_clarifier(self, user_input: str) -> dict:
        openai_client = get_openai_client()
        conv = openai_client.conversations.create(
            items=[{"type": "message", "role": "user", "content": user_input}],
        )
        response = openai_client.responses.create(
            conversation=conv.id,
            extra_body={
                "agent_reference": {
                    "name": settings.request_clarifier_agent,
                    "type": "agent_reference",
                }
            },
        )
        text = response.output_text
        try:
            openai_client.conversations.delete(conversation_id=conv.id)
        except Exception:
            pass

        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        raw = m.group(1) if m else text
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"complete": True, "enriched_request": user_input}

    @handler(input=str, output=ClarificationResult)
    async def run(self, user_input, ctx) -> None:
        result = await asyncio.to_thread(self._call_clarifier, user_input)
        ctx.set_state("original_input", user_input)
        cr = ClarificationResult(
            complete=result.get("complete", True),
            enriched_request=result.get("enriched_request", user_input),
            missing_fields=result.get("missing_fields", []),
            question=result.get("question", ""),
        )
        await ctx.send_message(cr)


class UserClarificationStep(Executor):
    """ClarificationResult (complete=false) → HITL でユーザーに追加情報を質問"""

    def __init__(self):
        super().__init__(id="user_clarification")

    @handler(input=ClarificationResult, output=str)
    async def handle_incomplete(self, result, ctx) -> None:
        ctx.set_state(
            "clarification_round", ctx.get_state("clarification_round", 0) + 1
        )
        await ctx.request_info(
            request_data=ClarificationHITLRequest(
                question=result.question or "追加情報を教えてください。",
                missing_fields=result.missing_fields,
                original_input=ctx.get_state("original_input", ""),
            ),
            response_type=ClarificationHITLResponse,
        )

    @response_handler
    async def handle_answer(
        self,
        original: ClarificationHITLRequest,
        response: ClarificationHITLResponse,
        ctx: WorkflowContext,
    ) -> None:
        round_num = ctx.get_state("clarification_round", 1)
        updated = f"{original.original_input}\n{response.answer}"
        ctx.set_state("original_input", updated)
        if round_num >= 3:
            await ctx.send_message(updated, target_id="clarification_direct")
        else:
            await ctx.send_message(updated, target_id="request_clarifier")


class ClarificationToRequestStep(Executor):
    """ClarificationResult (complete=true) → HITL で整理済みリクエストを確認"""

    def __init__(self):
        super().__init__(id="clarification_to_request")

    @handler(input=ClarificationResult, output=str)
    async def run(self, result, ctx) -> None:
        await ctx.request_info(
            request_data=RequestConfirmHITLRequest(
                enriched_request=result.enriched_request,
            ),
            response_type=RequestConfirmHITLResponse,
        )

    @response_handler
    async def handle_confirm(
        self,
        original: RequestConfirmHITLRequest,
        response: RequestConfirmHITLResponse,
        ctx: WorkflowContext,
    ) -> None:
        await ctx.send_message(original.enriched_request)


class ClarificationDirectStep(Executor):
    """確認ラウンド上限 → そのまま TravelPlanner へ"""

    def __init__(self):
        super().__init__(id="clarification_direct")

    @handler(input=str, output=str)
    async def run(self, text, ctx) -> None:
        await ctx.send_message(text)


# ---------------------------------------------------------------------------
# 条件分岐
# ---------------------------------------------------------------------------
def is_clarification_complete(message: Any) -> bool:
    return isinstance(message, ClarificationResult) and message.complete


def is_clarification_incomplete(message: Any) -> bool:
    return isinstance(message, ClarificationResult) and not message.complete
