"""ワークフロー実行ランナー

BackgroundTasks から呼ばれ、ワークフローを実行し、
進行状況をイベントストアに追記する。
"""

import json
import logging
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
from app.workflow.models import ClarificationHITLRequest, PlanReviewRequest
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
        await event_store.append(
            conversation_id=conversation_id,
            event_type="status",
            data=json.dumps(
                {"step": "start", "label": "ワークフロー開始..."}, ensure_ascii=False
            ),
            message_id=message_id,
        )

        builder = create_workflow_builder()

        workflow = builder.build()
        messages = [Message(role="user", contents=[user_input])]
        events = await workflow.run(messages)

        await _handle_workflow_result(
            events, conversation_id, event_store, conv_store,
            original_input=user_input,
        )

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


async def resume_workflow_async(
    conversation_id: str,
    user_input: str,
    message_id: str,
) -> None:
    """HITL 停止中のワークフローを再開する。

    - clarification: オリジナル入力 + 回答で再実行
    - plan_review (OK): プランを最終出力として完了
    - plan_review (変更): オリジナル入力 + 変更要望で再実行
    """
    event_store = get_event_store()
    conv_store = get_conversation_store()

    try:
        await event_store.append(
            conversation_id=conversation_id,
            event_type="status",
            data=json.dumps(
                {"step": "resume", "label": "ワークフロー再開..."}, ensure_ascii=False
            ),
            message_id=message_id,
        )

        conv = await conv_store.get(conversation_id)
        hitl_step = conv.get("hitl_step", "clarification") if conv else "clarification"
        original_input = conv.get("original_input", "") if conv else ""
        plan_text = conv.get("plan_text", "") if conv else ""

        # プラン確認で承認 → 規約チェック → 申請送信
        is_approved = user_input.strip().lower() in (
            "ok", "yes", "y", "はい", "確定", "進めて", "大丈夫",
        )
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
