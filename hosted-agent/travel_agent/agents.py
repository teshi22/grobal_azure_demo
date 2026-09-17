"""Connect the workflow to version-pinned Foundry Prompt Agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_framework.foundry import FoundryAgent

from .submission_agent import MCPSubmissionAgent


class PromptAgentSettings(Protocol):
    azure_ai_model_deployment_name: str
    clarifier_agent_name: str
    clarifier_agent_version: str
    planner_agent_name: str
    planner_agent_version: str
    plan_reviewer_agent_name: str
    plan_reviewer_agent_version: str
    policy_agent_name: str
    policy_agent_version: str
    approval_agent_name: str
    approval_agent_version: str
    mcp_tool_endpoint: str
    mcp_connection_id: str


@dataclass(frozen=True)
class TravelAgents:
    clarifier: FoundryAgent
    planner: FoundryAgent
    plan_reviewer: FoundryAgent
    policy: FoundryAgent
    approval: FoundryAgent
    submission: MCPSubmissionAgent | None = None
    versions: Mapping[str, str] = field(default_factory=dict)


def _required_version(name: str, version: str) -> str:
    pinned = version.strip()
    if not pinned:
        raise ValueError(f"A pinned version is required for Prompt Agent {name}")
    return pinned


def create_agents(
    *,
    project_endpoint: str,
    credential: Any,
    config: PromptAgentSettings,
) -> TravelAgents:
    connections = (
        (
            "clarifier",
            config.clarifier_agent_name,
            config.clarifier_agent_version,
        ),
        (
            "planner",
            config.planner_agent_name,
            config.planner_agent_version,
        ),
        (
            "plan_reviewer",
            config.plan_reviewer_agent_name,
            config.plan_reviewer_agent_version,
        ),
        (
            "policy",
            config.policy_agent_name,
            config.policy_agent_version,
        ),
        (
            "approval",
            config.approval_agent_name,
            config.approval_agent_version,
        ),
    )
    agents: dict[str, FoundryAgent] = {}
    versions: dict[str, str] = {}
    for role, agent_name, configured_version in connections:
        version = _required_version(agent_name, configured_version)
        agents[role] = FoundryAgent(
            project_endpoint=project_endpoint,
            agent_name=agent_name,
            agent_version=version,
            credential=credential,
            name=role,
            default_options={"store": False},
        )
        versions[agent_name] = version

    return TravelAgents(
        clarifier=agents["clarifier"],
        planner=agents["planner"],
        plan_reviewer=agents["plan_reviewer"],
        policy=agents["policy"],
        approval=agents["approval"],
        submission=MCPSubmissionAgent(
            project_endpoint=project_endpoint,
            model=config.azure_ai_model_deployment_name,
            server_url=config.mcp_tool_endpoint,
            connection_id=config.mcp_connection_id,
            credential=credential,
        ),
        versions=versions,
    )
