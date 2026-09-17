"""Context-scoped Cosmos checkpoint provider for ResponsesHostServer."""

from __future__ import annotations

from dataclasses import replace

from agent_framework import (
    CheckpointID,
    CheckpointStorage,
    InMemoryCheckpointStorage,
    WorkflowCheckpoint,
)
from agent_framework_azure_cosmos import CosmosCheckpointStorage
from agent_framework_foundry_hosting import ContextScopedStoreProvider
from azure.ai.agentserver.core import AgentConfig, FoundryAgentRequestContext
from azure.identity.aio import DefaultAzureCredential

from .settings import settings

_ALLOWED_CHECKPOINT_TYPES = (
    "travel_agent.models:ClarificationResult",
    "travel_agent.models:TravelPlan",
    "travel_agent.models:PolicyOutcome",
    "travel_agent.models:ApprovalDocument",
    "travel_agent.models:ClarificationRequest",
    "travel_agent.models:ClarificationResponse",
    "travel_agent.models:RequestConfirmationRequest",
    "travel_agent.models:RequestConfirmationResponse",
    "travel_agent.models:PlanReviewRequest",
    "travel_agent.models:PlanReviewResponse",
    "travel_agent.models:SubmissionApprovalRequest",
)


class ScopedCheckpointStorage:
    """Isolate a shared Cosmos checkpoint container by host context ID."""

    def __init__(self, storage: CheckpointStorage, context_id: str) -> None:
        self._storage = storage
        self._context_id = context_id

    def _scoped_name(self, workflow_name: str) -> str:
        return f"{self._context_id}:{workflow_name}"

    def _scope(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        return replace(
            checkpoint,
            workflow_name=self._scoped_name(checkpoint.workflow_name),
        )

    def _unscope(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        prefix = f"{self._context_id}:"
        workflow_name = checkpoint.workflow_name
        if not workflow_name.startswith(prefix):
            raise ValueError("Checkpoint belongs to a different workflow context")
        return replace(checkpoint, workflow_name=workflow_name[len(prefix) :])

    async def save(self, checkpoint: WorkflowCheckpoint) -> CheckpointID:
        return await self._storage.save(self._scope(checkpoint))

    async def load(self, checkpoint_id: CheckpointID) -> WorkflowCheckpoint:
        return self._unscope(await self._storage.load(checkpoint_id))

    async def list_checkpoints(
        self, *, workflow_name: str
    ) -> list[WorkflowCheckpoint]:
        checkpoints = await self._storage.list_checkpoints(
            workflow_name=self._scoped_name(workflow_name)
        )
        return [self._unscope(checkpoint) for checkpoint in checkpoints]

    async def delete(self, checkpoint_id: CheckpointID) -> bool:
        await self.load(checkpoint_id)
        return await self._storage.delete(checkpoint_id)

    async def get_latest(
        self, *, workflow_name: str
    ) -> WorkflowCheckpoint | None:
        checkpoint = await self._storage.get_latest(
            workflow_name=self._scoped_name(workflow_name)
        )
        return self._unscope(checkpoint) if checkpoint else None

    async def list_checkpoint_ids(
        self, *, workflow_name: str
    ) -> list[CheckpointID]:
        return await self._storage.list_checkpoint_ids(
            workflow_name=self._scoped_name(workflow_name)
        )


class CosmosCheckpointStoreProvider(
    ContextScopedStoreProvider[CheckpointStorage]
):
    """Use Cosmos in Foundry and isolated in-memory stores for local runs."""

    def __init__(self) -> None:
        self._cosmos_storage: CosmosCheckpointStorage | None = None
        self._local_stores: dict[str, InMemoryCheckpointStorage] = {}

    def get_store(
        self,
        *,
        config: AgentConfig,
        context_id: str,
        platform_context: FoundryAgentRequestContext,
    ) -> CheckpointStorage:
        del platform_context
        if not config.is_hosted or not settings.cosmos_endpoint:
            return self._local_stores.setdefault(
                context_id, InMemoryCheckpointStorage()
            )

        if self._cosmos_storage is None:
            self._cosmos_storage = CosmosCheckpointStorage(
                endpoint=settings.cosmos_endpoint,
                credential=DefaultAzureCredential(),
                database_name=settings.cosmos_database_name,
                container_name=settings.cosmos_checkpoint_container,
                allowed_checkpoint_types=list(_ALLOWED_CHECKPOINT_TYPES),
            )
        return ScopedCheckpointStorage(self._cosmos_storage, context_id)
