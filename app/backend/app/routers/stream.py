"""SSE ストリーミングエンドポイント

GET /api/conversations/{id}/stream — SSE でワークフローイベントを配信
"""

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.services.cosmos import get_event_bus, get_event_store

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

    - インメモリ EventBus (asyncio.Queue) から即時配信
    - Last-Event-ID 再接続時のみ Cosmos DB にフォールバック
    """
    event_store = get_event_store()
    bus = get_event_bus()

    async def event_generator():
        last_index = int(last_event_id) if last_event_id else -1

        # 初回接続・再接続: Cosmos から missed events を補完
        missed = await event_store.get_events_after(
            conversation_id, last_index
        )
        for event in missed:
            event_index = event["event_index"]
            last_index = event_index
            yield (
                f"id: {event_index}\n"
                f"event: {event['event_type']}\n"
                f"data: {event['data']}\n\n"
            )
            if event["event_type"] in ("complete", "error"):
                return

        # メイン配信: Queue から直接読む (Cosmos 読み取り不要)
        q = bus.subscribe(conversation_id)
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(
                        q.get(), timeout=SSE_HEARTBEAT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                except asyncio.CancelledError:
                    break

                event_index = event["event_index"]
                event_type = event["event_type"]
                data = event["data"]

                yield (
                    f"id: {event_index}\n"
                    f"event: {event_type}\n"
                    f"data: {data}\n\n"
                )

                if event_type in ("complete", "error"):
                    return
        finally:
            bus.unsubscribe(conversation_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
