"""出張申請登録ツール

申請書テキスト (JSON) を受け取り、Cosmos DB に登録する。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def submit_travel_request(arguments: dict) -> dict:
    """出張申請書を Cosmos DB に登録する

    Args:
        arguments: {
            "application_text": "申請書テキスト (JSON)",
            "conversation_id": "会話 ID (べき等性キー, 省略可)"
        }

    Returns:
        {"success": bool, "request_id": str, "message": str}
    """
    application_text = arguments.get("application_text", "")
    conversation_id = arguments.get("conversation_id", "")

    if not application_text:
        return {
            "success": False,
            "request_id": "",
            "message": "申請書テキストが空です",
        }

    # JSON パース（フォールバック: テキストそのまま保存）
    try:
        plan = json.loads(application_text)
    except (json.JSONDecodeError, TypeError):
        plan = {}

    request_id = f"TR-{uuid.uuid4().hex[:8].upper()}"
    submitted_at = datetime.now(timezone.utc).isoformat()

    doc = {
        "id": request_id,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "status": "submitted",
        "submitted_at": submitted_at,
        "application_text": application_text,
        # 構造化フィールド
        "departure": plan.get("departure", ""),
        "destination": plan.get("destination", ""),
        "schedule": plan.get("schedule", ""),
        "purpose": plan.get("purpose", ""),
        "trip_type": plan.get("trip_type", ""),
        "transportation_legs": plan.get("transportation_legs", []),
        "transportation_cost": plan.get("transportation_cost", 0),
        "hotel": plan.get("hotel", ""),
        "hotel_cost_per_night": plan.get("hotel_cost_per_night", 0),
        "hotel_nights": plan.get("hotel_nights", 0),
        "total_cost": plan.get("total_cost", 0),
    }

    # Cosmos DB に保存
    try:
        from tools.cosmos_client import get_container

        container = get_container()
        await container.create_item(doc)
        logger.info("Travel request saved to Cosmos DB: %s", request_id)
    except RuntimeError:
        # COSMOS_ENDPOINT 未設定 → ローカルスタブモード
        logger.warning("Cosmos DB not configured, returning stub response")
    except Exception as e:
        logger.error("Failed to save travel request: %s", e)
        return {
            "success": False,
            "request_id": request_id,
            "message": f"申請の保存に失敗しました: {e}",
        }

    return {
        "success": True,
        "request_id": request_id,
        "submitted_at": submitted_at,
        "message": f"出張申請 {request_id} を登録しました。",
    }
