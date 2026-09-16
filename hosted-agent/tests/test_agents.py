from types import SimpleNamespace

import pytest

import travel_agent.agents as agent_module


def _settings(**changes):
    values = {
        "clarifier_agent_name": "travel-request-clarifier",
        "clarifier_agent_version": "1",
        "planner_agent_name": "travel-request-planner",
        "planner_agent_version": "2",
        "policy_agent_name": "travel-request-policy-narrator",
        "policy_agent_version": "3",
        "approval_agent_name": "travel-request-approval-writer",
        "approval_agent_version": "4",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_create_agents_connects_to_pinned_foundry_versions(monkeypatch):
    calls = []

    def fake_foundry_agent(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(agent_module, "FoundryAgent", fake_foundry_agent)

    agents = agent_module.create_agents(
        project_endpoint="https://example.test/api/projects/test",
        credential=object(),
        config=_settings(),
    )

    assert [call["agent_version"] for call in calls] == ["1", "2", "3", "4"]
    assert all(call["default_options"] == {"store": False} for call in calls)
    assert agents.versions == {
        "travel-request-clarifier": "1",
        "travel-request-planner": "2",
        "travel-request-policy-narrator": "3",
        "travel-request-approval-writer": "4",
    }


def test_create_agents_rejects_unpinned_prompt_agent(monkeypatch):
    monkeypatch.setattr(
        agent_module,
        "FoundryAgent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    with pytest.raises(ValueError, match="pinned version"):
        agent_module.create_agents(
            project_endpoint="https://example.test/api/projects/test",
            credential=object(),
            config=_settings(planner_agent_version=""),
        )
