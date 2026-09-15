"""Configure the Agent Framework workflow host."""

import logging

from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.responses import ResponsesServerOptions
from azure.identity import DefaultAzureCredential

from .agents import create_agents
from .checkpoints import CosmosCheckpointStoreProvider
from .settings import settings
from .workflow import build_workflow

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
    client = FoundryChatClient(
        project_endpoint=settings.foundry_project_endpoint,
        model=settings.azure_ai_model_deployment_name,
        credential=DefaultAzureCredential(),
    )
    workflow_agent = build_workflow(create_agents(client)).as_agent(
        name="travel-request-workflow"
    )
    return ResponsesHostServer(
        workflow_agent,
        checkpoint_store_provider=CosmosCheckpointStoreProvider(),
        options=ResponsesServerOptions(resilient_background=True),
    )
