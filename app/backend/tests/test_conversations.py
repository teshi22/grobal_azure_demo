import os

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://example.services.ai.azure.com/api/projects/test",
)

from app.config import settings
from app.main import app
from app.routers import conversations


class _ConversationStore:
    def __init__(self):
        self.created: list[dict] = []

    async def create(
        self,
        conversation_id,
        user_id,
        *,
        scenario,
    ):
        self.created.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "scenario": scenario,
            }
        )


def test_conversation_creation_supports_both_scenarios(monkeypatch):
    store = _ConversationStore()
    monkeypatch.setattr(settings, "evaluation_mode", "stub")
    monkeypatch.setattr(settings, "app_environment", "development")
    monkeypatch.setattr(settings, "entra_tenant_id", "")
    monkeypatch.setattr(settings, "entra_client_id", "")
    monkeypatch.setattr(
        conversations,
        "get_conversation_store",
        lambda: store,
    )

    with TestClient(app) as client:
        default_response = client.post("/api/conversations", json={})
        workflow_response = client.post(
            "/api/conversations",
            json={"scenario": "agent_framework_workflow"},
        )
        single_response = client.post(
            "/api/conversations",
            json={"scenario": "single_prompt_agent"},
        )

    assert default_response.status_code == 200
    assert workflow_response.status_code == 200
    assert single_response.status_code == 200
    assert [item["scenario"] for item in store.created] == [
        "agent_framework_workflow",
        "agent_framework_workflow",
        "single_prompt_agent",
    ]
