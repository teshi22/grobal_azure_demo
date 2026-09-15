"""アプリケーション設定"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """環境変数から読み込む設定"""

    # Azure AI Foundry
    azure_ai_project_endpoint: str
    azure_ai_model_deployment_name: str = "gpt-5.4"
    hosted_agent_name: str = "travel-request-agent"

    # Cosmos DB
    cosmos_endpoint: str = ""
    cosmos_database: str = "travel-agent"
    cosmos_checkpoint_container: str = "workflow-checkpoints"
    cosmos_event_container: str = "conversation-events"
    cosmos_conversation_container: str = "conversations"
    cosmos_travel_request_container: str = "travel-requests"
    cosmos_approval_grant_container: str = "approval-grants"

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
