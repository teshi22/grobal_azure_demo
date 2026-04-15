"""アプリケーション設定"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """環境変数から読み込む設定"""

    # Azure AI Foundry
    azure_ai_project_endpoint: str
    azure_ai_model_deployment_name: str = "gpt-5.4"

    # Foundry Agent 名
    request_clarifier_agent: str = "RequestClarifier"
    travel_planner_agent: str = "TravelPlanner"
    policy_checker_agent: str = "PolicyChecker"
    approval_agent: str = "ApprovalAgent"

    # Bing 接続
    bing_project_connection_id: str = ""

    # Cosmos DB
    cosmos_endpoint: str = ""
    cosmos_database: str = "travel-agent"
    cosmos_checkpoint_container: str = "workflow-checkpoints"
    cosmos_event_container: str = "conversation-events"
    cosmos_conversation_container: str = "conversations"

    # MCP ツール
    mcp_tool_endpoint: str = ""

    # Application Insights
    applicationinsights_connection_string: str = ""

    # CORS (ローカル開発用。本番では同一オリジンのため空リストでOK)
    cors_origins: list[str] = []

    # Auth
    entra_tenant_id: str = ""
    entra_client_id: str = ""

    # Docs (本番では無効化推奨)
    enable_docs: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
