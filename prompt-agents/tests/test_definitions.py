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
        definition = agent_spec.build_definition("gpt-test")
        serialized = definition.as_dict()
        assert serialized["kind"] == "prompt"
        _assert_no_max_properties(serialized)
