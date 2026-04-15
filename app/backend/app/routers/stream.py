"""SSE ストリーミングエンドポイント

GET /api/conversations/{id}/stream — SSE でワークフローイベントを配信
"""

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.services.cosmos import get_event_store

router = APIRouter(tags=["stream"])
logger = logging.getLogger(__name__)

SSE_HEARTBEAT_INTERVAL = 15  # seconds


@router.get("/conversations/{conversation_id}/stream")
async def stream_events(
    conversation_id: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    """SSE でワークフローイベントをストリーミング配信する

    - Last-Event-ID ヘッダーで再接続時に途中から再配信
    - ハートビートで接続維持
    """
    event_store = get_event_store()

    async def event_generator():
        last_index = int(last_event_id) if last_event_id else -1

        while True:
            if await request.is_disconnected():
                break

            events = await event_store.get_events_after(
                conversation_id, last_index
            )

            for event in events:
                event_index = event["event_index"]
                event_type = event["event_type"]
                data = event["data"]
                last_index = event_index

                yield (
                    f"id: {event_index}\n"
                    f"event: {event_type}\n"
                    f"data: {data}\n\n"
                )

                # 完了/エラーイベントで終了
                if event_type in ("complete", "error"):
                    return

            # ハートビート
            yield ": heartbeat\n\n"

            try:
                await asyncio.sleep(SSE_HEARTBEAT_INTERVAL)
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
