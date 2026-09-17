"""Configure the Agent Framework workflow host."""

import logging

from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.responses import ResponsesServerOptions
from azure.identity import DefaultAzureCredential

from .agents import create_agents
from .checkpoints import CosmosCheckpointStoreProvider
from .settings import settings
from .workflow import build_workflow
from .workflow_agent import ChatCompatibleWorkflowAgent

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("agent_framework").setLevel(logging.WARNING)
logging.getLogger("agent_framework_azure_cosmos").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("hypercorn").setLevel(logging.WARNING)
logging.getLogger("microsoft.opentelemetry").setLevel(logging.WARNING)
logging.getLogger("opentelemetry").setLevel(logging.WARNING)


def create_server() -> ResponsesHostServer:
    credential = DefaultAzureCredential()
    agents = create_agents(
        project_endpoint=settings.foundry_project_endpoint,
        credential=credential,
        config=settings,
    )
    workflow_agent = ChatCompatibleWorkflowAgent(
        build_workflow(agents),
        name="travel-request-workflow"
    )
    return ResponsesHostServer(
        workflow_agent,
        checkpoint_store_provider=CosmosCheckpointStoreProvider(),
        options=ResponsesServerOptions(resilient_background=True),
    )
