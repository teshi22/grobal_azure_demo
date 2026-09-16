"""アプリケーション設定"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """環境変数から読み込む設定"""

    # Azure AI Foundry
    azure_ai_project_endpoint: str
    azure_ai_model_deployment_name: str = "gpt-5.4"
    hosted_agent_name: str = "travel-request-agent"
    hosted_agent_version: str = ""
    single_prompt_agent_name: str = "travel-request-single-agent"
    single_prompt_agent_version: str = ""
    evaluation_judge_model: str = "gpt-5.4"
    evaluation_mode: Literal["foundry", "stub"] = "foundry"
    app_environment: str = "development"
    evaluation_retry_attempts: int = 3
    evaluation_retry_base_seconds: float = 1.0

    # Cosmos DB
    cosmos_endpoint: str = ""
    cosmos_database: str = "travel-agent"
    cosmos_checkpoint_container: str = "workflow-checkpoints"
    cosmos_event_container: str = "conversation-events"
    cosmos_conversation_container: str = "conversations"
    cosmos_travel_request_container: str = "travel-requests"
    cosmos_approval_grant_container: str = "approval-grants"
    cosmos_evaluation_case_container: str = "evaluation-cases"
    cosmos_evaluation_run_container: str = "evaluation-runs"
    cosmos_evaluation_result_container: str = "evaluation-results"

    model_pricing_path: str = str(
        Path(__file__).resolve().parents[1] / "config" / "model-pricing.json"
    )
    evaluation_rubric_path: str = str(
        Path(__file__).resolve().parents[1]
        / "config"
        / "travel-request-rubric.json"
    )
    evaluation_seed_path: str = str(
        Path(__file__).resolve().parents[1]
        / "config"
        / "travel-request-cases.jsonl"
    )
    evaluation_temp_dir: str = str(
        Path(__file__).resolve().parents[1] / ".evaluation-tmp"
    )

    # Application Insights
    applicationinsights_connection_string: str = ""

    # CORS (ローカル開発用。本番では同一オリジンのため空リストでOK)
    cors_origins: list[str] = []

    # Auth
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_issuer: str = ""

    # Docs (本番では無効化推奨)
    enable_docs: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
