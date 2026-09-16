"""Deterministic scoring and configured token cost estimates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from app.config import settings
from app.schemas.evaluation import (
    CanonicalEvaluationOutput,
    DeterministicCheck,
    EvaluationCase,
    EvaluationScenario,
)

ALLOWED_FARE_DOMAINS = (
    "jreast.co.jp",
    "jr-central.co.jp",
    "jr-odekake.net",
    "jrkyushu.co.jp",
    "jrhokkaido.co.jp",
    "jr-shikoku.co.jp",
    "smart-ex.jp",
    "eki-net.com",
    "tokyometro.jp",
    "kotsu.metro.tokyo.jp",
    "ekitan.com",
    "transit.yahoo.co.jp",
    "jorudan.co.jp",
    "navitime.co.jp",
)


@dataclass(frozen=True)
class DeterministicScore:
    score: float
    checks: list[DeterministicCheck]
    output: CanonicalEvaluationOutput | None


def _extract_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ValueError("Evaluation output must be a JSON object or string")
    candidate = value.strip()
    fenced = re.search(r"```json\s*(.*?)\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        obj = re.search(r"\{.*\}", candidate, re.DOTALL)
        if obj:
            candidate = obj.group(0)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("Evaluation output JSON must be an object")
    return parsed


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s、,。・（）()年月日/-]+", "", value).casefold()


def _text_matches(expected: str, actual: str) -> bool:
    if not expected:
        return not actual.strip()
    normalized_expected = _normalize_text(expected)
    normalized_actual = _normalize_text(actual)
    return (
        normalized_expected in normalized_actual
        or normalized_actual in normalized_expected
    )


def _allowed_fare_source(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower().removeprefix("www.")
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in ALLOWED_FARE_DOMAINS
    )


def _check(
    name: str,
    passed: bool,
    score: float,
    detail: str = "",
) -> DeterministicCheck:
    return DeterministicCheck(
        name=name,
        passed=passed,
        score=score if passed else 0,
        detail=detail,
    )


def score_evaluation_output(
    case: EvaluationCase,
    raw_output: Any,
    scenario: EvaluationScenario,
) -> DeterministicScore:
    checks: list[DeterministicCheck] = []
    try:
        output = CanonicalEvaluationOutput.model_validate(
            _extract_json_object(raw_output)
        )
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        checks.append(_check("schema", False, 20, str(exc)))
        return DeterministicScore(score=0, checks=checks, output=None)

    diagnostics_passed = (
        output.diagnostics.case_id == case.id
        and output.diagnostics.scenario == scenario
    )
    checks.append(
        _check(
            "schema",
            diagnostics_passed,
            20,
            (
                f"expected_case_id={case.id}, "
                f"actual_case_id={output.diagnostics.case_id}, "
                f"expected_scenario={scenario}, "
                f"actual_scenario={output.diagnostics.scenario}"
            ),
        )
    )
    checks.append(
        _check(
            "status",
            output.status == case.expected_status,
            20,
            f"expected={case.expected_status}, actual={output.status}",
        )
    )

    expected = case.expected_request
    actual = output.request
    request_results = {
        "departure": bool(
            actual and _text_matches(expected.departure, actual.departure)
        ),
        "destination": bool(
            actual and _text_matches(expected.destination, actual.destination)
        ),
        "schedule": bool(
            actual and _text_matches(expected.schedule, actual.schedule)
        ),
        "purpose": bool(
            actual and _text_matches(expected.purpose, actual.purpose)
        ),
    }
    request_passed = actual is not None and all(request_results.values())
    checks.append(
        _check(
            "request_fields",
            request_passed,
            20,
            json.dumps(request_results, ensure_ascii=False),
        )
    )

    if case.expected_status == "needs_clarification":
        actual_questions = _normalize_text(
            " ".join(output.clarification_questions)
        )
        expected_questions = case.expected_clarification_fields
        clarification_passed = bool(expected_questions) and all(
            _normalize_text(field) in actual_questions
            for field in expected_questions
        )
        payload_empty = (
            output.itinerary is None
            and output.fare_total is None
            and not output.application_draft.strip()
        )
        checks.append(
            _check(
                "clarification",
                clarification_passed and payload_empty,
                20,
                json.dumps(
                    {
                        "expected_questions": expected_questions,
                        "payload_empty": payload_empty,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        policy_passed = output.policy.compliant is None
        checks.append(
            _check(
                "policy",
                policy_passed,
                20,
                f"actual={output.policy.compliant}",
            )
        )
    else:
        itinerary = output.itinerary
        fare_passed = False
        fare_detail = "itinerary missing"
        if itinerary is not None:
            costs = [leg.cost for leg in itinerary.transportation_legs]
            sources_valid = all(
                leg.fare_type.strip()
                and leg.source_title.strip()
                and _allowed_fare_source(leg.source_url)
                for leg in itinerary.transportation_legs
            )
            fare_passed = (
                sum(costs) == itinerary.transportation_cost
                and output.fare_total == itinerary.transportation_cost
                and sources_valid
            )
            fare_detail = json.dumps(
                {
                    "leg_total": sum(costs),
                    "transportation_cost": itinerary.transportation_cost,
                    "fare_total": output.fare_total,
                    "sources_valid": sources_valid,
                },
                ensure_ascii=False,
            )
        checks.append(
            _check("fare_evidence_and_total", fare_passed, 20, fare_detail)
        )
        expected_policy = case.expected_policy_compliant
        policy_passed = (
            expected_policy is None
            or output.policy.compliant == expected_policy
        )
        checks.append(
            _check(
                "policy",
                policy_passed,
                20,
                (
                    f"expected={expected_policy}, "
                    f"actual={output.policy.compliant}"
                ),
            )
        )

    return DeterministicScore(
        score=round(sum(check.score for check in checks), 2),
        checks=checks,
        output=output,
    )


def composite_score(
    rubric_score: float | None,
    deterministic_score: float | None,
) -> float | None:
    if rubric_score is None or deterministic_score is None:
        return None
    return round((rubric_score * 0.60) + (deterministic_score * 0.40), 2)


def load_model_pricing(path: str | Path | None = None) -> dict[str, Any]:
    pricing_path = Path(path or settings.model_pricing_path)
    with pricing_path.open(encoding="utf-8") as file:
        pricing = json.load(file)
    if pricing.get("unit") != "per_1m_tokens":
        raise ValueError("Model pricing unit must be per_1m_tokens")
    if not pricing.get("currency") or not pricing.get("updated_at"):
        raise ValueError("Model pricing requires currency and updated_at")
    if not isinstance(pricing.get("models"), dict):
        raise ValueError("Model pricing requires a models object")
    return pricing


def estimate_token_cost(
    per_model_usage: dict[str, Any],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    currency = pricing["currency"]
    total = 0.0
    unavailable_models: list[str] = []
    breakdown: dict[str, Any] = {}

    for model, usage in per_model_usage.items():
        model_price = pricing["models"].get(model)
        input_tokens = int(
            usage.get("input_tokens", usage.get("prompt_tokens", 0))
        )
        input_details = usage.get("input_tokens_details") or {}
        cached_input_tokens = int(
            usage.get(
                "cached_input_tokens",
                usage.get(
                    "cached_tokens",
                    input_details.get("cached_tokens", 0),
                ),
            )
        )
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        output_tokens = int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        )
        if (
            not model_price
            or model_price.get("input") is None
            or model_price.get("output") is None
        ):
            unavailable_models.append(model)
            breakdown[model] = {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "cost": None,
            }
            continue
        cost = (
            uncached_input_tokens * float(model_price["input"])
            + cached_input_tokens
            * float(model_price.get("cached_input", model_price["input"]))
            + output_tokens * float(model_price["output"])
        ) / 1_000_000
        total += cost
        breakdown[model] = {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "cost": round(cost, 8),
        }

    return {
        "available": not unavailable_models,
        "currency": currency,
        "total": round(total, 8) if not unavailable_models else None,
        "pricing_updated_at": pricing["updated_at"],
        "unavailable_models": sorted(unavailable_models),
        "breakdown": breakdown,
    }
