"""出張申請登録ツール

申請書テキストを受け取り、申請システムに登録する。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def submit_travel_request(arguments: dict) -> dict:
    """出張申請書を申請システムに登録する

    Args:
        arguments: {"application_text": "申請書テキスト"}

    Returns:
        {"success": bool, "request_id": str, "message": str}
    """
    application_text = arguments.get("application_text", "")

    if not application_text:
        return {
            "success": False,
            "request_id": "",
            "message": "申請書テキストが空です",
        }

    # TODO: 実際の申請システム API への送信ロジック
    # ここではスタブとして ID を発行して成功を返す
    request_id = f"TR-{uuid.uuid4().hex[:8].upper()}"
    submitted_at = datetime.now(timezone.utc).isoformat()

    logger.info(f"Travel request submitted: {request_id}")

    return {
        "success": True,
        "request_id": request_id,
        "submitted_at": submitted_at,
        "message": f"出張申請 {request_id} を登録しました。",
    }
