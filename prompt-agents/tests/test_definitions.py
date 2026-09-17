from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


DEFINITIONS_PATH = Path(__file__).resolve().parents[1] / "definitions.py"


def _load_definitions() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "prompt_agent_definitions_under_test",
        DEFINITIONS_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_no_max_properties(value: Any) -> None:
    if isinstance(value, dict):
        assert "maxProperties" not in value
        for child in value.values():
            _assert_no_max_properties(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_max_properties(child)


def _assert_strict_object_schemas(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
            assert set(value.get("required", [])) == set(
                value.get("properties", {})
            )
        for child in value.values():
            _assert_strict_object_schemas(child)
    elif isinstance(value, list):
        for child in value:
            _assert_strict_object_schemas(child)


def test_prompt_agent_definitions_use_supported_strict_schemas():
    definitions = _load_definitions()

    _assert_no_max_properties(definitions.EVALUATION_OUTPUT_SCHEMA)
    diagnostics = definitions.EVALUATION_OUTPUT_SCHEMA["properties"][
        "diagnostics"
    ]
    agent_versions = diagnostics["properties"]["agent_versions"]
    assert agent_versions == {
        "type": "object",
        "properties": {
            "single_prompt_agent": {"type": "string"},
        },
        "required": ["single_prompt_agent"],
        "additionalProperties": False,
    }

    for agent_spec in definitions.PROMPT_AGENT_SPECS:
        definition = agent_spec.build_definition(
            "gpt-test",
            mcp_connection_id="travel-mcp-connection",
            mcp_server_url="https://example.test/mcp",
            web_iq_connection_id="web-iq-connection",
            web_iq_server_url="https://example.test/web-iq/mcp",
        )
        serialized = definition.as_dict()
        assert serialized["kind"] == "prompt"
        _assert_no_max_properties(serialized)


def test_single_agent_uses_web_iq_and_submission_mcp_tools():
    definitions = _load_definitions()
    agent_spec = next(
        spec
        for spec in definitions.PROMPT_AGENT_SPECS
        if spec.name == "travel-request-single-agent"
    )

    serialized = agent_spec.build_definition(
        "gpt-test",
        mcp_connection_id="travel-mcp-connection",
        mcp_server_url="https://example.test/mcp",
        web_iq_connection_id="web-iq-connection",
        web_iq_server_url="https://example.test/web-iq/mcp",
    ).as_dict()
    tools = serialized["tools"]
    assert {tool["type"] for tool in tools} == {"mcp"}
    assert len(tools) == 2
    assert "tool_choice" not in serialized
    assert "text" not in serialized

    tools_by_label = {tool["server_label"]: tool for tool in tools}
    assert tools_by_label["travel-request-submission"] == {
        "server_label": "travel-request-submission",
        "server_url": "https://example.test/mcp",
        "project_connection_id": "travel-mcp-connection",
        "allowed_tools": [
            "prepare_travel_request_submission",
            "submit_travel_request_with_approval",
        ],
        "require_approval": "never",
        "type": "mcp",
    }
    assert tools_by_label["travel-web-iq"] == {
        "server_label": "travel-web-iq",
        "server_url": "https://example.test/web-iq/mcp",
        "project_connection_id": "web-iq-connection",
        "allowed_tools": ["web", "browse"],
        "require_approval": "never",
        "type": "mcp",
    }


def test_single_agent_prompt_enforces_conversation_and_tool_order():
    definitions = _load_definitions()
    agent_spec = next(
        spec
        for spec in definitions.PROMPT_AGENT_SPECS
        if spec.name == "travel-request-single-agent"
    )
    instructions = agent_spec.instructions

    assert "submission_token" not in instructions
    assert '"mode"' not in instructions
    assert "1回の利用者メッセージで複数段階を飛ばさない" in instructions
    assert "依頼内容の明示的な承認を得た後だけ" in instructions
    assert "`travel-web-iq` MCPの`web`と`browse`" in instructions
    assert "出発時刻を指定していない場合は、往路を午前10時ごろ出発" in instructions
    assert "検索結果のスニペットだけで運賃を確定せず" in instructions
    assert "旅程の明示的な承認を得た後だけ" in instructions
    assert "申請書や旅程をsubmitへ渡したり、書き換えたりしません" in instructions
    assert "`confirmation_text`には、利用者の回答を要約・修正せず" in instructions
    assert instructions.index("## 3. 申請内容を固定") < instructions.index(
        "## 4. 承認済み申請を登録"
    )


def test_evaluation_response_schema_remains_foundry_compatible():
    definitions = _load_definitions()
    agent_spec = next(
        spec
        for spec in definitions.PROMPT_AGENT_SPECS
        if spec.name == "travel-request-single-evaluator"
    )
    serialized = agent_spec.build_definition(
        "gpt-test",
        mcp_connection_id="travel-mcp-connection",
        mcp_server_url="https://example.test/mcp",
        web_iq_connection_id="web-iq-connection",
        web_iq_server_url="https://example.test/web-iq/mcp",
    ).as_dict()
    assert {tool["type"] for tool in serialized["tools"]} == {"web_search"}
    response_format = serialized["text"]["format"]
    assert response_format == {
        "name": "travel_evaluation_output",
        "schema": definitions.EVALUATION_OUTPUT_SCHEMA,
        "strict": True,
        "type": "json_schema",
    }
    _assert_no_max_properties(response_format["schema"])
    _assert_strict_object_schemas(response_format["schema"])
