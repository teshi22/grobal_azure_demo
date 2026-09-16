"""Connect the workflow to version-pinned Foundry Prompt Agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_framework.foundry import FoundryAgent


class PromptAgentSettings(Protocol):
    clarifier_agent_name: str
    clarifier_agent_version: str
    planner_agent_name: str
    planner_agent_version: str
    policy_agent_name: str
    policy_agent_version: str
    approval_agent_name: str
    approval_agent_version: str


@dataclass(frozen=True)
class TravelAgents:
    clarifier: FoundryAgent
    planner: FoundryAgent
    policy: FoundryAgent
    approval: FoundryAgent
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
        policy=agents["policy"],
        approval=agents["approval"],
        versions=versions,
    )
