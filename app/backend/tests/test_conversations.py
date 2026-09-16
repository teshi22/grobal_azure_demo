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
        interaction_mode,
    ):
        self.created.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "scenario": scenario,
                "interaction_mode": interaction_mode,
            }
        )


def test_conversation_creation_defaults_and_playground_scenarios(monkeypatch):
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
            json={
                "scenario": "agent_framework_workflow",
                "interaction_mode": "playground",
            },
        )
        single_response = client.post(
            "/api/conversations",
            json={
                "scenario": "single_prompt_agent",
                "interaction_mode": "playground",
            },
        )
        unsupported_response = client.post(
            "/api/conversations",
            json={
                "scenario": "single_prompt_agent",
                "interaction_mode": "submission",
            },
        )

    assert default_response.status_code == 200
    assert workflow_response.status_code == 200
    assert single_response.status_code == 200
    assert unsupported_response.status_code == 422
    assert [
        (item["scenario"], item["interaction_mode"])
        for item in store.created
    ] == [
        ("agent_framework_workflow", "submission"),
        ("agent_framework_workflow", "playground"),
        ("single_prompt_agent", "playground"),
    ]
