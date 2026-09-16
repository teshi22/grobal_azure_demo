"""Schemas for side-by-side Foundry evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EvaluationStatus = Literal[
    "needs_clarification",
    "draft_ready",
    "policy_blocked",
    "error",
]
EvaluationScenario = Literal[
    "agent_framework_workflow",
    "single_prompt_agent",
]
HumanWinner = Literal[
    "agent_framework_workflow",
    "single_prompt_agent",
    "tie",
]
EVALUATION_DATASET_ID = "travel-request-v1"
EvaluationDatasetId = Literal["travel-request-v1"]
MAX_EVALUATION_CASES_PER_RUN = 100
MAX_EVALUATION_INPUT_LENGTH = 16_000


class ExpectedRequest(BaseModel):
    departure: str = ""
    destination: str = ""
    schedule: str = ""
    purpose: str = ""


class EvaluationLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = ""
    method: str
    from_: str = Field(alias="from")
    to: str
    cost: int = Field(ge=0)
    fare_type: str
    source_url: str
    source_title: str


class EvaluationItinerary(BaseModel):
    model_config = ConfigDict(extra="allow")

    departure: str
    destination: str
    purpose: str
    schedule: str
    trip_type: Literal["日帰り", "宿泊"]
    transportation_legs: list[EvaluationLeg] = Field(min_length=1)
    transportation_cost: int = Field(ge=0)
    total_cost: int = Field(ge=0)


class EvaluationPolicyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compliant: bool | None = None
    details: list[str] = Field(default_factory=list)
    narrative: str = ""


class EvaluationCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    fare_type: str = ""


class EvaluationDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
    scenario: EvaluationScenario
    case_id: str
    agent_versions: dict[str, str] = Field(default_factory=dict)


class CanonicalEvaluationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EvaluationStatus
    request: ExpectedRequest | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    itinerary: EvaluationItinerary | None = None
    fare_total: int | None = Field(default=None, ge=0)
    policy: EvaluationPolicyOutput = Field(
        default_factory=EvaluationPolicyOutput
    )
    application_draft: str = ""
    citations: list[EvaluationCitation] = Field(default_factory=list)
    diagnostics: EvaluationDiagnostics

    @model_validator(mode="after")
    def validate_status_payload(self) -> "CanonicalEvaluationOutput":
        if self.status == "needs_clarification":
            if not self.clarification_questions:
                raise ValueError(
                    "needs_clarification requires clarification_questions"
                )
            return self
        if self.status == "draft_ready":
            if self.itinerary is None or not self.application_draft.strip():
                raise ValueError(
                    "draft_ready requires itinerary and application_draft"
                )
        return self


class EvaluationCaseBase(BaseModel):
    dataset_id: EvaluationDatasetId = EVALUATION_DATASET_ID
    title: str = Field(min_length=1, max_length=160)
    input: str = Field(min_length=1, max_length=MAX_EVALUATION_INPUT_LENGTH)
    expected_status: EvaluationStatus
    expected_request: ExpectedRequest = Field(default_factory=ExpectedRequest)
    expected_policy_compliant: bool | None = None
    expected_clarification_fields: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})


class EvaluationCaseCreate(EvaluationCaseBase):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")


class EvaluationCaseUpdate(EvaluationCaseBase):
    version: int = Field(ge=1)


class EvaluationCase(EvaluationCaseCreate):
    version: int = 1
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EvaluationCaseImportRequest(BaseModel):
    cases: list[EvaluationCaseCreate] = Field(
        default_factory=list,
        max_length=1000,
    )
    jsonl: str = ""
    seed_default: bool = False
    overwrite: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> "EvaluationCaseImportRequest":
        source_count = sum(
            (
                bool(self.cases),
                bool(self.jsonl.strip()),
                self.seed_default,
            )
        )
        if source_count != 1:
            raise ValueError(
                "Provide exactly one of cases, jsonl, or seed_default"
            )
        return self


class EvaluationCaseImportResponse(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    cases: list["EvaluationCase"] = Field(default_factory=list)


class EvaluationRunCreate(BaseModel):
    dataset_id: EvaluationDatasetId = EVALUATION_DATASET_ID
    case_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_EVALUATION_CASES_PER_RUN,
    )
    name: str = Field(default="", max_length=160)

    @field_validator("case_ids")
    @classmethod
    def unique_case_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class DeterministicCheck(BaseModel):
    name: str
    passed: bool
    score: float = Field(ge=0, le=100)
    detail: str = ""


class HumanReview(BaseModel):
    agent_framework_score: int = Field(ge=1, le=5)
    single_agent_score: int = Field(ge=1, le=5)
    winner: HumanWinner
    comment: str = Field(default="", max_length=4000)
    reviewer_id: str = ""
    reviewer_name: str = ""
    reviewed_at: datetime | None = None


class HumanReviewUpdate(HumanReview):
    version: int = Field(ge=1)


class EvaluationResult(BaseModel):
    comparison_id: str
    case_id: str
    scenario: EvaluationScenario
    output: dict[str, Any] = Field(default_factory=dict)
    rubric_score: float | None = Field(default=None, ge=0, le=100)
    deterministic_score: float | None = Field(default=None, ge=0, le=100)
    overall_score: float | None = Field(default=None, ge=0, le=100)
    deterministic_checks: list[DeterministicCheck] = Field(default_factory=list)
    evaluator_scores: dict[str, float | int | str | None] = Field(
        default_factory=dict
    )
    evaluator_results: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: dict[str, Any] = Field(default_factory=dict)
    raw_output: Any = None
    error: str = ""
    human_review: HumanReview | None = None


class EvaluationScenarioResults(BaseModel):
    agent_framework_workflow: EvaluationResult | None = None
    single_prompt_agent: EvaluationResult | None = None


class EvaluationCaseResult(BaseModel):
    id: str
    comparison_id: str
    case_id: str
    case_title: str = ""
    case_input: str = ""
    scenarios: EvaluationScenarioResults = Field(
        default_factory=EvaluationScenarioResults
    )
    scenario_statuses: dict[EvaluationScenario, str] = Field(
        default_factory=dict
    )
    scenario_errors: dict[EvaluationScenario, str] = Field(
        default_factory=dict
    )
    human_review: HumanReview | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EvaluationRun(BaseModel):
    id: str
    name: str
    status: str
    dataset_id: str
    dataset_version: str
    foundry_dataset_id: str = ""
    foundry_evaluation_id: str
    evaluation_definition_hash: str = ""
    scenario_runs: dict[EvaluationScenario, dict[str, Any]]
    case_ids: list[str] = Field(default_factory=list)
    result_documents_state: str = ""
    aggregate: dict[str, Any] = Field(default_factory=dict)
    pricing_updated_at: str = ""
    created_by: str = ""
    error: str = ""
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
