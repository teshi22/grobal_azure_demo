import asyncio

from app.services.conversation_local import (
    LocalConversationStore,
    LocalEventStore,
)


def test_local_conversation_store_claims_and_updates_messages():
    asyncio.run(_test_local_conversation_store_claims_and_updates_messages())


async def _test_local_conversation_store_claims_and_updates_messages():
    store = LocalConversationStore()
    await store.create(
        "conversation-1",
        "user-1",
        scenario="single_prompt_agent",
    )

    assert await store.claim_message(
        "conversation-1",
        "key-1",
        message_id="message-1",
        user_id="user-1",
        content="大阪へ出張したい",
    )
    assert not await store.claim_message(
        "conversation-1",
        "key-1",
        message_id="message-2",
        user_id="user-1",
        content="duplicate",
    )

    pending = await store.claim_pending_message("conversation-1")
    assert pending == {
        "message_id": "message-1",
        "user_id": "user-1",
        "content": "大阪へ出張したい",
        "idempotency_key": "key-1",
        "approval_request_id": None,
        "approve": None,
    }
    await store.update(
        "conversation-1",
        status="awaiting_input",
        active_message=None,
        processing_lease_until=None,
    )

    conversation = await store.get_owned("conversation-1", "user-1")
    assert conversation is not None
    assert conversation["scenario"] == "single_prompt_agent"
    assert conversation["status"] == "awaiting_input"
    assert await store.get_owned("conversation-1", "user-2") is None


def test_local_event_store_replays_and_deduplicates_events():
    asyncio.run(_test_local_event_store_replays_and_deduplicates_events())


async def _test_local_event_store_replays_and_deduplicates_events():
    store = LocalEventStore()
    first_cursor = await store.append(
        "conversation-1",
        "user_message",
        {"content": "大阪へ出張したい"},
        message_id="message-1",
        idempotency_key="key-1",
    )
    await store.append(
        "conversation-1",
        "hitl_request",
        {"type": "clarification"},
        message_id="message-1",
    )

    events = await store.get_events_after("conversation-1", first_cursor)
    duplicate = await store.get_by_idempotency_key(
        "conversation-1",
        "key-1",
    )

    assert [event["event_type"] for event in events] == ["hitl_request"]
    assert duplicate is not None
    assert duplicate["message_id"] == "message-1"
    assert duplicate["data"] == '{"content": "大阪へ出張したい"}'
