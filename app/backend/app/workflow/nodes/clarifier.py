"""RequestClarifier ノード — Foundry Agent で4項目を抽出し、ロジックで過不足を判定"""

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
    ExtractedRequest,
    RequestConfirmHITLRequest,
    RequestConfirmHITLResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS = ["departure", "destination", "schedule", "purpose"]
_FIELD_LABELS = {
    "departure": "出発地",
    "destination": "目的地",
    "schedule": "日程",
    "purpose": "出張目的",
}

DEFAULT_DEPARTURE = "大阪"


# ---------------------------------------------------------------------------
# Foundry Agent による情報抽出
# ---------------------------------------------------------------------------
def _extract_fields(user_input: str) -> ExtractedRequest:
    """RequestClarifier Foundry Agent でユーザー入力から4項目を抽出する"""
    openai_client = get_openai_client()
    try:
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
        logger.info("Agent raw response: %s", text[:500])

        try:
            openai_client.conversations.delete(conversation_id=conv.id)
        except Exception:
            pass

        return _parse_agent_response(text)

    except Exception as e:
        logger.warning("Foundry Agent call failed, returning empty: %s", e)
        return ExtractedRequest()


def _parse_agent_response(text: str) -> ExtractedRequest:
    """Agent の応答テキストから JSON を抽出して ExtractedRequest に変換"""
    # 1. ```json ... ``` ブロックを優先抽出
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        # 2. フォールバック: テキスト中の JSON オブジェクト {...} を探す
        m2 = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        raw = m2.group(0) if m2 else text

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed from agent response: %s", text[:300])
        return ExtractedRequest()

    result = ExtractedRequest(
        departure=str(data.get("departure", "") or "").strip(),
        destination=str(data.get("destination", "") or "").strip(),
        schedule=str(data.get("schedule", "") or "").strip(),
        purpose=str(data.get("purpose", "") or "").strip(),
    )
    logger.info("Parsed fields: %s", result.model_dump())
    return result


def _merge_fields(
    new_fields: dict[str, str],
    prev_fields: dict[str, str],
) -> dict[str, str]:
    """新しい抽出結果と前回の結果をマージ（新しい値を優先）"""
    merged: dict[str, str] = {}
    for key in _REQUIRED_FIELDS:
        merged[key] = new_fields.get(key) or prev_fields.get(key) or ""
    return merged


def _check_completeness(
    fields: dict[str, str],
) -> tuple[bool, list[str], str]:
    """必須フィールドが揃っているかロジックで判定"""
    missing_labels = [
        _FIELD_LABELS[key] for key in _REQUIRED_FIELDS if not fields.get(key)
    ]
    if missing_labels:
        question = f"{'、'.join(missing_labels)}を教えてください。"
        return False, missing_labels, question
    return True, [], ""


def _build_enriched_request(fields: dict[str, str]) -> str:
    """TravelPlanner 向けのラベル付きリクエスト文を生成"""
    lines = [
        f"出発地: {fields['departure']}",
        f"目的地: {fields['destination']}",
        f"日程: {fields['schedule']}",
        f"目的: {fields['purpose']}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ワークフローノード
# ---------------------------------------------------------------------------
class RequestClarifierStep(Executor):
    """Step 0: LLM 構造化出力で情報抽出 → ロジックで完全性チェック"""

    def __init__(self):
        super().__init__(id="request_clarifier")

    @handler(input=str, output=ClarificationResult)
    async def run(self, user_input, ctx) -> None:
        extracted = await asyncio.to_thread(_extract_fields, user_input)

        # 前回の抽出結果とマージ（追加入力・修正に対応）
        prev_fields = ctx.get_state("request_fields") or {}
        new_fields = extracted.model_dump()
        fields = _merge_fields(new_fields, prev_fields)

        # 出発地のデフォルト適用
        departure_is_default = False
        if not fields["departure"]:
            fields["departure"] = DEFAULT_DEPARTURE
            departure_is_default = True

        ctx.set_state("request_fields", fields)
        ctx.set_state("departure_is_default", departure_is_default)
        ctx.set_state("original_input", user_input)

        complete, missing_labels, question = _check_completeness(fields)

        logger.info(
            "Extraction: fields=%s, complete=%s, missing=%s",
            fields, complete, missing_labels,
        )

        cr = ClarificationResult(
            complete=complete,
            enriched_request=_build_enriched_request(fields) if complete else "",
            missing_fields=missing_labels,
            question=question,
        )
        await ctx.send_message(cr)


class UserClarificationStep(Executor):
    """ClarificationResult (complete=false) → HITL でユーザーに追加情報を質問"""

    def __init__(self):
        super().__init__(id="user_clarification")

    @handler(input=ClarificationResult, output=str)
    async def handle_incomplete(self, result, ctx) -> None:
        round_num = ctx.get_state("clarification_round", 0) + 1
        ctx.set_state("clarification_round", round_num)
        await ctx.request_info(
            request_data=ClarificationHITLRequest(
                question=result.question or "追加情報を教えてください。",
                missing_fields=result.missing_fields,
                original_input=ctx.get_state("original_input", ""),
                extracted_fields=ctx.get_state("request_fields") or {},
                departure_is_default=ctx.get_state("departure_is_default", False),
                clarification_round=round_num,
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
        # 元の入力 + 追加回答を結合して再抽出
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
        fields = ctx.get_state("request_fields", {}) or {}
        departure_is_default = ctx.get_state("departure_is_default", False)

        departure_display = fields.get("departure", "")
        if departure_is_default:
            departure_display += "（既定値）"

        await ctx.request_info(
            request_data=RequestConfirmHITLRequest(
                enriched_request=result.enriched_request,
                departure=departure_display,
                destination=fields.get("destination", ""),
                schedule=fields.get("schedule", ""),
                purpose=fields.get("purpose", ""),
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
