"""Serializable workflow and HITL models."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import TypedDict

from .fare_sources import is_allowed_fare_source


APPROVAL_WORDS = frozenset(
    {"ok", "yes", "y", "はい", "確定", "進めて", "大丈夫", "承認"}
)
_EMPTY_NUMERIC_VALUES = frozenset({"", "なし", "不要", "null", "none", "-"})
_TOTAL_KEYS = (
    "total_jpy",
    "total_cost",
    "total",
    "round_trip_jpy",
    "roundtrip_jpy",
    "amount_jpy",
    "amount",
    "cost",
)
_LEG_DIRECTION_KEYS = ("direction", "segment")
_LEG_METHOD_KEYS = ("method", "mode")
_LEG_FROM_KEYS = (
    "from",
    "origin",
    "departure_point",
    "departure_station",
)
_LEG_TO_KEYS = (
    "to",
    "destination",
    "arrival_point",
    "arrival_station",
)
_LEG_COST_KEYS = ("cost", "fare_jpy", "fare", "price_jpy", "price")
_LEG_FARE_TYPE_KEYS = ("fare_type", "ticket_type", "fare_basis")
_LEG_SOURCE_URL_KEYS = ("source_url", "fare_source_url", "url")
_LEG_SOURCE_TITLE_KEYS = ("source_title", "fare_source_title", "title")

TransportationLeg = TypedDict(
    "TransportationLeg",
    {
        "direction": str,
        "method": str,
        "from": str,
        "to": str,
        "cost": int | None,
        "fare_type": str,
        "source_url": str,
        "source_title": str,
    },
    total=False,
)


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _number_from_value(value: Any) -> float | Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _EMPTY_NUMERIC_VALUES:
            return None
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", normalized)
        if match:
            return float(match.group(0).replace(",", ""))
        return value
    if isinstance(value, dict):
        for key in _TOTAL_KEYS:
            if key in value:
                normalized = _number_from_value(value[key])
                if isinstance(normalized, float):
                    return normalized
        components = [
            normalized
            for item in value.values()
            if isinstance((normalized := _number_from_value(item)), float)
        ]
        if components:
            return sum(components)
    return value


class ExtractedRequest(BaseModel):
    departure: str = ""
    destination: str = ""
    schedule: str = ""
    purpose: str = ""


class ClarificationResult(BaseModel):
    complete: bool
    enriched_request: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    question: str = ""


class TravelPlan(BaseModel):
    departure: str
    destination: str
    purpose: str
    schedule: str
    trip_type: Literal["日帰り", "宿泊"]
    transportation_legs: list[TransportationLeg] = Field(default_factory=list)
    transportation_cost: int = 0
    hotel: str | None = None
    hotel_cost_per_night: int | None = None
    hotel_nights: int | None = None
    total_cost: int = 0
    distance_km: float = 0
    travel_time_hours: float = 0
    fare_basis: str = "IC優先"
    searched_at: str = ""

    @field_validator("trip_type", mode="before")
    @classmethod
    def normalize_trip_type(cls, value: Any) -> Any:
        text = str(value)
        if "日帰り" in text:
            return "日帰り"
        if "宿泊" in text:
            return "宿泊"
        return value

    @field_validator("transportation_legs", mode="before")
    @classmethod
    def normalize_transportation_legs(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value

        legs: list[Any] = []
        for item in value:
            if not isinstance(item, dict):
                legs.append(item)
                continue

            leg = dict(item)
            leg["direction"] = _first_text(item, _LEG_DIRECTION_KEYS)
            leg["method"] = _first_text(item, _LEG_METHOD_KEYS)
            leg["from"] = _first_text(item, _LEG_FROM_KEYS)
            leg["to"] = _first_text(item, _LEG_TO_KEYS)
            leg["fare_type"] = _first_text(item, _LEG_FARE_TYPE_KEYS)
            leg["source_url"] = _first_text(item, _LEG_SOURCE_URL_KEYS)
            leg["source_title"] = _first_text(item, _LEG_SOURCE_TITLE_KEYS)

            raw_cost = next(
                (item[key] for key in _LEG_COST_KEYS if key in item),
                None,
            )
            cost = _number_from_value(raw_cost)
            leg["cost"] = (
                int(round(cost)) if isinstance(cost, float) else None
            )
            legs.append(leg)
        return legs

    @model_validator(mode="after")
    def reconcile_cost_totals(self) -> "TravelPlan":
        itemized_costs = [
            leg.get("cost")
            for leg in self.transportation_legs
            if isinstance(leg.get("cost"), int)
        ]
        if (
            self.transportation_legs
            and len(itemized_costs) == len(self.transportation_legs)
        ):
            self.transportation_cost = sum(itemized_costs)

        hotel_total = (self.hotel_cost_per_night or 0) * (
            self.hotel_nights or 0
        )
        self.total_cost = self.transportation_cost + hotel_total
        return self

    @field_validator("transportation_cost", "total_cost", mode="before")
    @classmethod
    def normalize_required_cost(cls, value: Any) -> Any:
        normalized = _number_from_value(value)
        return int(round(normalized)) if isinstance(normalized, float) else value

    @field_validator("hotel_cost_per_night", "hotel_nights", mode="before")
    @classmethod
    def normalize_optional_integer(cls, value: Any) -> Any:
        normalized = _number_from_value(value)
        if normalized is None:
            return None
        return int(round(normalized)) if isinstance(normalized, float) else value

    @field_validator("distance_km", "travel_time_hours", mode="before")
    @classmethod
    def normalize_measurement(cls, value: Any) -> Any:
        normalized = _number_from_value(value)
        return normalized if isinstance(normalized, float) else value


def fare_evidence_errors(plan: TravelPlan) -> list[str]:
    errors: list[str] = []
    if not plan.transportation_legs:
        return ["transportation_legs must contain at least one searched route"]

    for index, leg in enumerate(plan.transportation_legs, start=1):
        label = f"transportation_legs[{index}]"
        for key in ("method", "from", "to"):
            if not str(leg.get(key, "")).strip():
                errors.append(f"{label}.{key} is required")

        cost = leg.get("cost")
        if not isinstance(cost, int) or cost < 0:
            errors.append(f"{label}.cost must be an integer fare")
        if not str(leg.get("fare_type", "")).strip():
            errors.append(f"{label}.fare_type is required")

        source_url = str(leg.get("source_url", "")).strip()
        if not is_allowed_fare_source(source_url):
            errors.append(
                f"{label}.source_url must be an approved HTTPS fare source"
            )
    return errors


class PolicyOutcome(BaseModel):
    compliant: bool
    details: list[str]
    narrative: str
    plan_json: str


class ApprovalDocument(BaseModel):
    application_text: str
    plan_json: str
    policy_narrative: str
    plan_hash: str
    approval_id: str = ""


EvaluationStatus = Literal[
    "needs_clarification",
    "draft_ready",
    "policy_blocked",
    "error",
]


class EvaluationInputEnvelope(BaseModel):
    mode: Literal["evaluation"]
    schema_version: Literal["1"] = "1"
    case_id: str = Field(min_length=1)
    input: str = Field(min_length=1)


class PlaygroundInputEnvelope(BaseModel):
    mode: Literal["playground"]
    conversation_id: str = Field(min_length=1)
    input: str = Field(min_length=1)


class EvaluationPolicyResult(BaseModel):
    compliant: bool | None = None
    details: list[str] = Field(default_factory=list)
    narrative: str = ""


class EvaluationCitation(BaseModel):
    url: str
    title: str
    fare_type: str = ""


class EvaluationDiagnostics(BaseModel):
    schema_version: Literal["1"] = "1"
    scenario: Literal["agent_framework_workflow"]
    case_id: str
    agent_versions: dict[str, str] = Field(default_factory=dict)


class EvaluationOutput(BaseModel):
    status: EvaluationStatus
    request: ExtractedRequest | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    itinerary: TravelPlan | None = None
    fare_total: int | None = None
    policy: EvaluationPolicyResult = Field(
        default_factory=EvaluationPolicyResult
    )
    application_draft: str = ""
    citations: list[EvaluationCitation] = Field(default_factory=list)
    diagnostics: EvaluationDiagnostics

    @model_validator(mode="after")
    def reconcile_fare_total(self) -> "EvaluationOutput":
        if self.itinerary is not None:
            self.fare_total = self.itinerary.transportation_cost
        return self


@dataclass
class ClarificationRequest:
    question: str
    missing_fields: list[str]
    original_input: str
    message: str
    data: dict[str, Any]
    type: str = field(init=False, default="clarification")

    def convert_to_payload(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "message": self.message,
                "data": self.data,
            },
            ensure_ascii=False,
        )


@dataclass
class ClarificationResponse:
    answer: str

    @staticmethod
    def convert_from_payload(payload: str) -> "ClarificationResponse":
        try:
            data = json.loads(payload)
            return ClarificationResponse(answer=str(data.get("answer", "")).strip())
        except json.JSONDecodeError:
            return ClarificationResponse(answer=payload.strip())


@dataclass
class RequestConfirmationRequest:
    enriched_request: str
    fields: dict[str, str]
    message: str
    data: dict[str, Any]
    type: str = field(init=False, default="request_confirmation")

    def convert_to_payload(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "message": self.message,
                "data": self.data,
            },
            ensure_ascii=False,
        )


@dataclass
class RequestConfirmationResponse:
    confirmed: bool
    revision: str = ""

    @staticmethod
    def convert_from_payload(payload: str) -> "RequestConfirmationResponse":
        try:
            data = json.loads(payload)
            return RequestConfirmationResponse(
                confirmed=bool(data.get("confirmed", False)),
                revision=str(data.get("revision", "")).strip(),
            )
        except json.JSONDecodeError:
            text = payload.strip()
            return RequestConfirmationResponse(
                confirmed=text.lower() in APPROVAL_WORDS,
                revision="" if text.lower() in APPROVAL_WORDS else text,
            )


@dataclass
class PlanReviewRequest:
    plan_json: str
    message: str
    data: dict[str, Any]
    type: str = field(init=False, default="plan_review")

    def convert_to_payload(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "message": self.message,
                "data": self.data,
            },
            ensure_ascii=False,
        )


@dataclass
class PlanReviewResponse:
    approved: bool
    feedback: str = ""

    @staticmethod
    def convert_from_payload(payload: str) -> "PlanReviewResponse":
        try:
            data = json.loads(payload)
            return PlanReviewResponse(
                approved=bool(data.get("approved", False)),
                feedback=str(data.get("feedback", "")).strip(),
            )
        except json.JSONDecodeError:
            text = payload.strip()
            return PlanReviewResponse(
                approved=text.lower() in APPROVAL_WORDS,
                feedback="" if text.lower() in APPROVAL_WORDS else text,
            )


@dataclass
class SubmissionConfirmationRequest:
    approval_id: str
    application_text: str
    plan_json: str
    policy_narrative: str
    message: str
    data: dict[str, Any]
    type: str = field(init=False, default="submit_confirmation")

    def convert_to_payload(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "message": self.message,
                "data": self.data,
            },
            ensure_ascii=False,
        )


@dataclass
class SubmissionConfirmationResponse:
    confirmation_text: str

    @staticmethod
    def convert_from_payload(payload: str) -> "SubmissionConfirmationResponse":
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return SubmissionConfirmationResponse(
                confirmation_text=payload.strip(),
            )
        return SubmissionConfirmationResponse(
            confirmation_text=str(
                data.get("confirmation_text")
                or data.get("input")
                or data.get("answer")
                or ""
            ).strip(),
        )
