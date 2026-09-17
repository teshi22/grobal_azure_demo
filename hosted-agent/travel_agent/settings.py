"""Hosted Agent runtime settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    foundry_project_endpoint: str
    azure_ai_model_deployment_name: str = "gpt-5.6-luna"

    clarifier_agent_name: str = "travel-request-clarifier"
    clarifier_agent_version: str = ""
    planner_agent_name: str = "travel-request-planner"
    planner_agent_version: str = ""
    policy_agent_name: str = "travel-request-policy-narrator"
    policy_agent_version: str = ""
    approval_agent_name: str = "travel-request-approval-writer"
    approval_agent_version: str = ""

    cosmos_endpoint: str = ""
    cosmos_database_name: str = "travel-agent"
    cosmos_checkpoint_container: str = "workflow-checkpoints"
    mcp_tool_endpoint: str = ""
    mcp_function_app_client_id: str = ""

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }


settings = Settings()
