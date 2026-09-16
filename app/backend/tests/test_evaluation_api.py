from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_local_stub_api_completes_paired_run_without_azure(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_mode", "stub")
    monkeypatch.setattr(settings, "app_environment", "development")
    monkeypatch.setattr(settings, "entra_tenant_id", "")
    monkeypatch.setattr(settings, "entra_client_id", "")

    with TestClient(app) as client:
        cases_response = client.get("/api/evaluations/cases")
        assert cases_response.status_code == 200
        assert len(cases_response.json()) == 20

        create_response = client.post(
            "/api/evaluations/runs",
            json={
                "name": "Local comparison",
                "case_ids": ["case-standard-tokyo-osaka"],
            },
        )
        assert create_response.status_code == 201
        comparison_id = create_response.json()["id"]

        run_response = client.get(
            f"/api/evaluations/runs/{comparison_id}"
        )
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "completed"

        results_response = client.get(
            f"/api/evaluations/runs/{comparison_id}/results"
        )
        assert results_response.status_code == 200
        result = results_response.json()[0]
        assert result["scenarios"]["agent_framework_workflow"] is not None
        assert result["scenarios"]["single_prompt_agent"] is not None

        review_response = client.put(
            (
                f"/api/evaluations/runs/{comparison_id}/results/"
                "case-standard-tokyo-osaka/human-review"
            ),
            json={
                "version": result["version"],
                "agent_framework_score": 5,
                "single_agent_score": 4,
                "winner": "agent_framework_workflow",
                "comment": "The workflow result was clearer.",
            },
        )
        assert review_response.status_code == 200
        review = review_response.json()["human_review"]
        assert review["reviewer_id"] == "dev-user"


def test_evaluation_api_rejects_unsupported_dataset(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_mode", "stub")
    monkeypatch.setattr(settings, "app_environment", "development")
    monkeypatch.setattr(settings, "entra_tenant_id", "")
    monkeypatch.setattr(settings, "entra_client_id", "")

    with TestClient(app) as client:
        list_response = client.get(
            "/api/evaluations/cases?dataset_id=other-dataset"
        )
        create_response = client.post(
            "/api/evaluations/runs",
            json={"dataset_id": "other-dataset"},
        )

    assert list_response.status_code == 422
    assert create_response.status_code == 422
