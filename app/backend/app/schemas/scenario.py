"""Schemas for side-effect-free manual scenario runs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.evaluation import (
    MAX_EVALUATION_INPUT_LENGTH,
    CanonicalEvaluationOutput,
    EvaluationScenario,
)


class ScenarioRunRequest(BaseModel):
    scenario: EvaluationScenario
    input: str = Field(
        min_length=1,
        max_length=MAX_EVALUATION_INPUT_LENGTH,
    )

    @field_validator("input")
    @classmethod
    def normalize_input(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("input must not be blank")
        return normalized


class ScenarioRunResponse(BaseModel):
    run_id: str
    scenario: EvaluationScenario
    agent_name: str
    agent_version: str
    execution_mode: Literal["foundry", "stub"]
    duration_ms: int = Field(ge=0)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    output: CanonicalEvaluationOutput
