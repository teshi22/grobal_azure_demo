"""ワークフロー実行ランナー

BackgroundTasks から呼ばれ、ワークフローを実行し、
進行状況をイベントストアに追記する。
"""

import json
import logging
import traceback

from app.services.cosmos import (
    CosmosCheckpointRepository,
    get_conversation_store,
    get_event_store,
)
from app.workflow.builder import create_workflow_builder

logger = logging.getLogger(__name__)


async def run_workflow_async(
    conversation_id: str,
    user_input: str,
    message_id: str,
) -> None:
    """新規ワークフローを開始する (BackgroundTasks から呼び出し)"""
    event_store = get_event_store()
    conv_store = get_conversation_store()

    try:
        # 開始イベント
        await event_store.append(
            conversation_id=conversation_id,
            event_type="status",
            data=json.dumps(
                {"step": "start", "label": "ワークフロー開始..."}, ensure_ascii=False
            ),
            message_id=message_id,
        )

        builder = create_workflow_builder()
        checkpoint_repo = CosmosCheckpointRepository()

        # TODO: Agent Framework の run API に checkpoint_repo を渡す
        # 現在は builder.build().run() で実行
        workflow = builder.build()
        events = await workflow.run(user_input)
        outputs = events.get_outputs()

        if outputs:
            await event_store.append(
                conversation_id=conversation_id,
                event_type="complete",
                data=json.dumps({"output": outputs[-1]}, ensure_ascii=False),
            )
            await conv_store.update_status(conversation_id, "completed")
        else:
            # HITL 停止の場合
            await conv_store.update_status(conversation_id, "waiting_for_input")

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
    """HITL 停止中のワークフローを再開する (BackgroundTasks から呼び出し)"""
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

        checkpoint_repo = CosmosCheckpointRepository()

        # TODO: checkpoint から復元して @response_handler で再開
        # checkpoint_data = await checkpoint_repo.load(conversation_id)
        # builder = create_workflow_builder()
        # workflow = builder.build()
        # events = await workflow.resume(checkpoint_data, user_input)

        await conv_store.update_status(conversation_id, "processing")

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
