"""ワークフロー実行ランナー

BackgroundTasks から呼ばれ、ワークフローを実行し、
進行状況をイベントストアに追記する。
"""

import asyncio
import json
import logging
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone

from agent_framework import Message

from app.services.cosmos import (
    get_conversation_store,
    get_event_store,
)
from app.services.mcp_client import call_submit_tool
from app.workflow.builder import create_workflow_builder
from app.workflow.models import (
    ClarificationHITLRequest,
    PlanReviewRequest,
    RequestConfirmHITLRequest,
)
from app.workflow.nodes.clarifier import (
    DEFAULT_DEPARTURE,
    _build_enriched_request,
    _check_completeness,
    _extract_fields,
    _merge_fields,
)
from app.workflow.policy import evaluate_policy

logger = logging.getLogger(__name__)


def _format_plan_complete(plan_text: str) -> str:
    """プランJSONを読みやすい確定メッセージに整形する"""
    try:
        p = json.loads(plan_text)
        lines = ["✅ 出張申請プラン確定", ""]
        lines.append(f"📍 {p.get('departure', '—')} → {p.get('destination', '—')}")
        lines.append(f"📅 {p.get('schedule', '—')}")
        lines.append(f"🎯 目的: {p.get('purpose', '—')}")
        lines.append(f"種別: {p.get('trip_type', '—')}")
        lines.append("")
        for i, leg in enumerate(p.get("transportation_legs", []), 1):
            lines.append(
                f"  {i}. {leg.get('method', '—')}  "
                f"{leg.get('from', '—')} → {leg.get('to', '—')}  "
                f"¥{leg.get('cost', 0):,}"
            )
        lines.append(f"🚄 交通費計: ¥{p.get('transportation_cost', 0):,}")
        if p.get("trip_type") == "宿泊":
            lines.append(
                f"🏨 宿泊: {p.get('hotel', '—')} "
                f"¥{p.get('hotel_cost_per_night', 0):,}/泊 × {p.get('hotel_nights', 1)}泊"
            )
        lines.append(f"💰 合計: ¥{p.get('total_cost', 0):,}")
        return "\n".join(lines)
    except (json.JSONDecodeError, TypeError):
        return f"✅ 出張申請プラン確定\n\n{plan_text}"


async def _run_travel_planner_direct(
    enriched_request: str,
    conversation_id: str,
    event_store,
    conv_store,
) -> None:
    """リクエスト確認後: TravelPlanner Foundry Agent を直接呼び出し → PlanReview HITL"""
    import asyncio

    from app.config import settings
    from app.workflow.nodes.foundry_base import FoundryAgentNode

    t0 = time.perf_counter()
    await event_store.append(
        conversation_id=conversation_id,
        event_type="status",
        data=json.dumps(
            {"step": "travel_planner", "label": "旅程プランを検索中..."},
            ensure_ascii=False,
        ),
    )

    node = FoundryAgentNode(id="travel_planner", agent_name=settings.travel_planner_agent)
    plan_text, _ = await asyncio.to_thread(node._call_agent, enriched_request)
    t1 = time.perf_counter()
    logger.info("[PERF] TravelPlanner agent: %.3fs", t1 - t0)

    # PlanReview HITL イベントを発行
    plan_review_req = PlanReviewRequest(plan_text=plan_text)
    hitl_data = _hitl_request_to_event(plan_review_req)

    if hitl_data:
        await event_store.append(
            conversation_id=conversation_id,
            event_type="hitl_request",
            data=json.dumps(hitl_data, ensure_ascii=False),
        )

    # 次の resume 用コンテキスト保存
    conv = await conv_store.get(conversation_id)
    if conv:
        conv["hitl_step"] = "plan_review"
        conv["plan_text"] = plan_text
        conv["original_input"] = enriched_request
        conv["status"] = "waiting_for_input"
        conv["updated_at"] = datetime.now(timezone.utc).isoformat()
        await conv_store._container.upsert_item(conv)
    else:
        await conv_store.update_status(conversation_id, "waiting_for_input")
    t2 = time.perf_counter()
    logger.info("[PERF] TravelPlanner total: %.3fs", t2 - t0)


async def _run_policy_check_and_complete(
    plan_text: str,
    conversation_id: str,
    event_store,
    conv_store,
) -> None:
    """プラン承認後: 旅費規程チェック → MCP 申請送信 → 完了"""

    # --- 1. 旅費規程チェック ---
    await event_store.append(
        conversation_id=conversation_id,
        event_type="status",
        data=json.dumps(
            {"step": "policy_check", "label": "旅費規程チェック中..."},
            ensure_ascii=False,
        ),
    )

    try:
        plan = json.loads(plan_text)
    except (json.JSONDecodeError, TypeError):
        plan = {}

    policy_result = evaluate_policy({
        "hotel_cost_per_night": plan.get("hotel_cost_per_night", 0),
        "transportation": " ".join(
            leg.get("method", "") for leg in plan.get("transportation_legs", [])
        ),
        "distance_km": plan.get("distance_km", 0),
        "travel_time_hours": plan.get("travel_time_hours", 0),
        "needs_pre_night_stay": plan.get("needs_pre_night_stay", False),
    })

    compliant = policy_result["compliant"]
    details = policy_result["details"]
    policy_text_display = "\n".join(f"  {d}" for d in details)

    # 規程チェック結果を SSE で送出
    await event_store.append(
        conversation_id=conversation_id,
        event_type="agent_response",
        data=json.dumps(
            {"content": f"📋 旅費規程チェック結果\n\n{policy_text_display}"},
            ensure_ascii=False,
        ),
    )

    if not compliant:
        # 不適合 → 差し戻し
        output = (
            f"❌ 旅費規程チェック: 不適合\n\n{policy_text_display}"
            f"\n\nプランを修正して再申請してください。"
        )
        await event_store.append(
            conversation_id=conversation_id,
            event_type="complete",
            data=json.dumps({"output": output}, ensure_ascii=False),
        )
        await conv_store.update_status(conversation_id, "completed")
        return

    # --- 2. MCP 申請送信 ---
    await event_store.append(
        conversation_id=conversation_id,
        event_type="status",
        data=json.dumps(
            {"step": "submit", "label": "出張申請を送信中..."},
            ensure_ascii=False,
        ),
    )

    submit_result = await call_submit_tool({"application_text": plan_text})

    # --- 3. 完了メッセージ ---
    output = _format_plan_complete(plan_text)
    output += f"\n\n📋 規程チェック: 適合 ✅\n{policy_text_display}"

    status = submit_result.get("status")
    if status == "submitted":
        output += "\n\n📤 出張申請を申請システムへ送信しました！"
    elif status == "skipped":
        output += "\n\n⚠️ 申請システム未設定のため送信はスキップされました。"
    else:
        output += f"\n\n❌ 申請送信に失敗: {submit_result.get('message', '不明')}"

    await event_store.append(
        conversation_id=conversation_id,
        event_type="complete",
        data=json.dumps({"output": output}, ensure_ascii=False),
    )
    await conv_store.update_status(conversation_id, "completed")


def _hitl_request_to_event(request_data) -> dict | None:
    """HITL request_info データを SSE 用の dict に変換する"""
    if isinstance(request_data, ClarificationHITLRequest):
        return {
            "type": "clarification",
            "message": request_data.convert_to_payload(),
            "data": asdict(request_data),
        }
    elif isinstance(request_data, RequestConfirmHITLRequest):
        return {
            "type": "request_confirmation",
            "message": request_data.convert_to_payload(),
            "data": asdict(request_data),
        }
    elif isinstance(request_data, PlanReviewRequest):
        # plan_text は JSON 文字列 → 構造化データとしてフロントに渡す
        try:
            plan_data = json.loads(request_data.plan_text)
        except (json.JSONDecodeError, TypeError):
            plan_data = {"plan_text": request_data.plan_text}
        return {
            "type": "plan_review",
            "message": request_data.convert_to_payload(),
            "data": plan_data,
        }
    return None


async def _handle_workflow_result(
    events,
    conversation_id: str,
    event_store,
    conv_store,
    original_input: str = "",
):
    """ワークフロー結果を処理してイベントストアに書き込む"""
    outputs = events.get_outputs()
    hitl_events = events.get_request_info_events()

    if outputs:
        await event_store.append(
            conversation_id=conversation_id,
            event_type="complete",
            data=json.dumps({"output": outputs[-1]}, ensure_ascii=False),
        )
        await conv_store.update_status(conversation_id, "completed")
    elif hitl_events:
        hitl_event = hitl_events[-1]
        hitl_data = _hitl_request_to_event(hitl_event.data)
        hitl_type = hitl_data.get("type", "unknown") if hitl_data else "unknown"

        if hitl_data:
            await event_store.append(
                conversation_id=conversation_id,
                event_type="hitl_request",
                data=json.dumps(hitl_data, ensure_ascii=False),
            )

        # 再開時に使うコンテキストを保存
        conv = await conv_store.get(conversation_id)
        if conv:
            conv["original_input"] = original_input
            conv["hitl_step"] = hitl_type
            if isinstance(hitl_event.data, PlanReviewRequest):
                conv["plan_text"] = hitl_event.data.plan_text
            if isinstance(hitl_event.data, RequestConfirmHITLRequest):
                conv["enriched_request"] = hitl_event.data.enriched_request
            if isinstance(hitl_event.data, ClarificationHITLRequest):
                conv["extracted_fields"] = hitl_event.data.extracted_fields or {}
                conv["departure_is_default"] = hitl_event.data.departure_is_default
                conv["clarification_round"] = hitl_event.data.clarification_round
            conv["status"] = "waiting_for_input"
            conv["updated_at"] = datetime.now(timezone.utc).isoformat()
            await conv_store._container.upsert_item(conv)
        else:
            await conv_store.update_status(conversation_id, "waiting_for_input")
    else:
        await conv_store.update_status(conversation_id, "waiting_for_input")


async def run_workflow_async(
    conversation_id: str,
    user_input: str,
    message_id: str,
) -> None:
    """新規ワークフローを開始する (BackgroundTasks から呼び出し)"""
    event_store = get_event_store()
    conv_store = get_conversation_store()

    try:
        t0 = time.perf_counter()
        await event_store.append(
            conversation_id=conversation_id,
            event_type="status",
            data=json.dumps(
                {"step": "start", "label": "ワークフロー開始..."}, ensure_ascii=False
            ),
            message_id=message_id,
        )
        t1 = time.perf_counter()
        logger.info("[PERF] status append: %.3fs", t1 - t0)

        builder = create_workflow_builder()

        workflow = builder.build()
        messages = [Message(role="user", contents=[user_input])]
        events = await workflow.run(messages)
        t2 = time.perf_counter()
        logger.info("[PERF] workflow.run: %.3fs", t2 - t1)

        await _handle_workflow_result(
            events, conversation_id, event_store, conv_store,
            original_input=user_input,
        )
        t3 = time.perf_counter()
        logger.info("[PERF] handle_result: %.3fs | total: %.3fs", t3 - t2, t3 - t0)

    except Exception as e:
        logger.error(f"Workflow error: {e}\n{traceback.format_exc()}")
        await event_store.append(
            conversation_id=conversation_id,
            event_type="error",
            data=json.dumps(
                {"message": str(e), "step": "unknown"}, ensure_ascii=False
            ),
        )
        await conv_store.update_status(conversation_id, "error")


async def _run_clarification_direct(
    user_input: str,
    conversation_id: str,
    event_store,
    conv_store,
    conv: dict,
) -> None:
    """clarification 再開: 前回の抽出結果を保持し、新しい入力から差分抽出 → マージ"""
    prev_fields = conv.get("extracted_fields") or {}
    original_input = conv.get("original_input", "")
    departure_is_default = conv.get("departure_is_default", False)
    clarification_round = conv.get("clarification_round", 0) + 1

    # ユーザーの追加回答だけを Agent に渡して抽出
    extracted = await asyncio.to_thread(_extract_fields, user_input)
    new_fields = extracted.model_dump()
    fields = _merge_fields(new_fields, prev_fields)

    # 出発地のデフォルト適用
    if not fields["departure"]:
        fields["departure"] = DEFAULT_DEPARTURE
        departure_is_default = True

    updated_input = f"{original_input}\n{user_input}" if original_input else user_input

    complete, missing_labels, question = _check_completeness(fields)
    logger.info(
        "Clarification direct: fields=%s, complete=%s, missing=%s, round=%d",
        fields, complete, missing_labels, clarification_round,
    )

    if complete or clarification_round >= 3:
        # 全項目揃った → リクエスト確認 HITL
        enriched_request = _build_enriched_request(fields)
        departure_display = fields["departure"]
        if departure_is_default:
            departure_display += "（既定値）"

        confirm_req = RequestConfirmHITLRequest(
            enriched_request=enriched_request,
            departure=departure_display,
            destination=fields.get("destination", ""),
            schedule=fields.get("schedule", ""),
            purpose=fields.get("purpose", ""),
        )
        hitl_data = _hitl_request_to_event(confirm_req)
        if hitl_data:
            await event_store.append(
                conversation_id=conversation_id,
                event_type="hitl_request",
                data=json.dumps(hitl_data, ensure_ascii=False),
            )
        # Cosmos に保存
        conv["hitl_step"] = "request_confirmation"
        conv["enriched_request"] = enriched_request
        conv["original_input"] = updated_input
        conv["extracted_fields"] = fields
        conv["status"] = "waiting_for_input"
        conv["updated_at"] = datetime.now(timezone.utc).isoformat()
        await conv_store._container.upsert_item(conv)
    else:
        # まだ不足 → 再度 clarification HITL
        clarify_req = ClarificationHITLRequest(
            question=question,
            missing_fields=missing_labels,
            original_input=updated_input,
            extracted_fields=fields,
            departure_is_default=departure_is_default,
            clarification_round=clarification_round,
        )
        hitl_data = _hitl_request_to_event(clarify_req)
        if hitl_data:
            await event_store.append(
                conversation_id=conversation_id,
                event_type="hitl_request",
                data=json.dumps(hitl_data, ensure_ascii=False),
            )
        conv["hitl_step"] = "clarification"
        conv["original_input"] = updated_input
        conv["extracted_fields"] = fields
        conv["departure_is_default"] = departure_is_default
        conv["clarification_round"] = clarification_round
        conv["status"] = "waiting_for_input"
        conv["updated_at"] = datetime.now(timezone.utc).isoformat()
        await conv_store._container.upsert_item(conv)


async def resume_workflow_async(
    conversation_id: str,
    user_input: str,
    message_id: str,
) -> None:
    """HITL 停止中のワークフローを再開する。

    - clarification: 前回の抽出結果を保持しつつ追加入力から差分抽出
    - request_confirmation (OK): TravelPlanner を直接呼び出し → PlanReview
    - request_confirmation (修正): 修正内容で再実行
    - plan_review (OK): 規約チェック → 申請送信
    - plan_review (変更): オリジナル入力 + 変更要望で再実行
    """
    event_store = get_event_store()
    conv_store = get_conversation_store()

    try:
        t0 = time.perf_counter()
        await event_store.append(
            conversation_id=conversation_id,
            event_type="status",
            data=json.dumps(
                {"step": "resume", "label": "ワークフロー再開..."}, ensure_ascii=False
            ),
            message_id=message_id,
        )

        conv = await conv_store.get(conversation_id)
        t1 = time.perf_counter()
        logger.info("[PERF] resume setup (append+get): %.3fs", t1 - t0)
        hitl_step = conv.get("hitl_step", "clarification") if conv else "clarification"
        original_input = conv.get("original_input", "") if conv else ""
        plan_text = conv.get("plan_text", "") if conv else ""
        enriched_request = conv.get("enriched_request", "") if conv else ""

        is_approved = user_input.strip().lower() in (
            "ok", "yes", "y", "はい", "確定", "進めて", "大丈夫",
        )

        # clarification → 前回の抽出結果を保持して差分抽出
        if hitl_step == "clarification" and conv:
            await _run_clarification_direct(
                user_input, conversation_id, event_store, conv_store, conv,
            )
            return

        # リクエスト確認で承認 → TravelPlanner を直接呼び出し
        if hitl_step == "request_confirmation" and is_approved and enriched_request:
            await _run_travel_planner_direct(
                enriched_request, conversation_id, event_store, conv_store,
            )
            return

        # プラン確認で承認 → 規約チェック → 申請送信
        if hitl_step == "plan_review" and is_approved and plan_text:
            await _run_policy_check_and_complete(
                plan_text, conversation_id, event_store, conv_store,
            )
            return

        # それ以外 → ワークフロー再実行
        enriched_input = f"{original_input}\n{user_input}" if original_input else user_input

        builder = create_workflow_builder()
        workflow = builder.build()
        messages = [Message(role="user", contents=[enriched_input])]
        events = await workflow.run(messages)

        await _handle_workflow_result(
            events, conversation_id, event_store, conv_store,
            original_input=enriched_input,
        )

    except Exception as e:
        logger.error(f"Resume error: {e}\n{traceback.format_exc()}")
        await event_store.append(
            conversation_id=conversation_id,
            event_type="error",
            data=json.dumps(
                {"message": str(e), "step": "resume"}, ensure_ascii=False
            ),
        )
        await conv_store.update_status(conversation_id, "error")
