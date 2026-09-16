"""Authenticated, durable SSE event streaming."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.auth.entra import CurrentUser
from app.services.cosmos import get_conversation_store, get_event_store

router = APIRouter(tags=["stream"])

_TERMINAL_EVENT_TYPES = frozenset(
    {"agent_response", "hitl_request", "complete", "error"}
)
_INITIAL_POLL_SECONDS = 1
_MAX_POLL_SECONDS = 5
_KEEP_ALIVE_SECONDS = 15


@router.get("/conversations/{conversation_id}/stream")
async def stream_events(
    conversation_id: str,
    request: Request,
    current_user: CurrentUser,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    last_event_id_query: str | None = Query(default=None, alias="last_event_id"),
):
    conversation = await get_conversation_store().get_owned(
        conversation_id,
        current_user["sub"],
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_generator():
        cursor = last_event_id or last_event_id_query or ""
        poll_seconds = _INITIAL_POLL_SECONDS
        seconds_since_keep_alive = 0
        while not await request.is_disconnected():
            rows = await get_event_store().get_events_after(
                conversation_id,
                cursor,
            )
            reached_terminal_event = False
            for row in rows:
                cursor = row["event_cursor"]
                yield (
                    f"id: {cursor}\n"
                    f"event: {row['event_type']}\n"
                    f"data: {row['data']}\n\n"
                )
                if row["event_type"] in _TERMINAL_EVENT_TYPES:
                    reached_terminal_event = True
            if reached_terminal_event:
                return

            if rows:
                poll_seconds = _INITIAL_POLL_SECONDS
                seconds_since_keep_alive = 0
            else:
                seconds_since_keep_alive += poll_seconds
                poll_seconds = min(poll_seconds * 2, _MAX_POLL_SECONDS)
            if seconds_since_keep_alive >= _KEEP_ALIVE_SECONDS:
                yield ": keep-alive\n\n"
                seconds_since_keep_alive = 0
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
