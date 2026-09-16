import json
from pathlib import Path

from app.schemas.evaluation import EvaluationCase
from app.services.evaluation_scoring import (
    composite_score,
    estimate_token_cost,
    load_model_pricing,
    score_evaluation_output,
)


def _case(**changes):
    data = {
        "id": "case-001",
        "dataset_id": "travel-request-v1",
        "title": "東京から大阪",
        "input": "東京から大阪へ出張",
        "expected_status": "draft_ready",
        "expected_request": {
            "departure": "東京",
            "destination": "大阪",
            "schedule": "2026年10月20日",
            "purpose": "顧客会議",
        },
        "expected_policy_compliant": True,
        "expected_clarification_fields": [],
        "tags": ["standard"],
        "enabled": True,
        "version": 1,
    }
    data.update(changes)
    return EvaluationCase.model_validate(data)


def _draft_output():
    return {
        "status": "draft_ready",
        "request": {
            "departure": "東京",
            "destination": "大阪",
            "schedule": "2026-10-20（火）",
            "purpose": "顧客会議",
        },
        "clarification_questions": [],
        "itinerary": {
            "departure": "東京",
            "destination": "大阪",
            "purpose": "顧客会議",
            "schedule": "2026-10-20（火）",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "method": "東海道新幹線",
                    "from": "東京",
                    "to": "新大阪",
                    "cost": 14720,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                    "source_title": "スマートEX",
                },
                {
                    "direction": "復路",
                    "method": "東海道新幹線",
                    "from": "新大阪",
                    "to": "東京",
                    "cost": 14720,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                    "source_title": "スマートEX",
                },
            ],
            "transportation_cost": 29440,
            "total_cost": 29440,
        },
        "fare_total": 29440,
        "policy": {
            "compliant": True,
            "details": ["タクシー利用なし"],
            "narrative": "規程に適合しています。",
        },
        "application_draft": "顧客会議のため大阪へ出張します。",
        "citations": [],
        "diagnostics": {
            "schema_version": "1",
            "scenario": "agent_framework_workflow",
            "case_id": "case-001",
            "agent_versions": {"planner": "3"},
        },
    }


def test_scores_valid_draft_output_at_100():
    result = score_evaluation_output(
        _case(),
        _draft_output(),
        "agent_framework_workflow",
    )

    assert result.score == 100
    assert result.output is not None
    assert all(check.passed for check in result.checks)


def test_rejects_unapproved_fare_source_and_bad_total():
    output = _draft_output()
    output["itinerary"]["transportation_legs"][0]["source_url"] = (
        "https://example.com/fare"
    )
    output["fare_total"] = 1

    result = score_evaluation_output(
        _case(),
        output,
        "agent_framework_workflow",
    )

    fare_check = next(
        check
        for check in result.checks
        if check.name == "fare_evidence_and_total"
    )
    assert not fare_check.passed
    assert result.score == 80


def test_scores_clarification_without_requiring_itinerary():
    case = _case(
        expected_status="needs_clarification",
        expected_request={
            "departure": "東京",
            "destination": "",
            "schedule": "2026年10月20日",
            "purpose": "顧客会議",
        },
        expected_policy_compliant=None,
        expected_clarification_fields=["目的地"],
    )
    output = {
        "status": "needs_clarification",
        "request": {
            "departure": "東京",
            "destination": "",
            "schedule": "2026年10月20日",
            "purpose": "顧客会議",
        },
        "clarification_questions": ["目的地を教えてください。"],
        "itinerary": None,
        "fare_total": None,
        "policy": {"compliant": None, "details": [], "narrative": ""},
        "application_draft": "",
        "citations": [],
        "diagnostics": {
            "schema_version": "1",
            "scenario": "single_prompt_agent",
            "case_id": "case-001",
            "agent_versions": {"single": "2"},
        },
    }

    assert score_evaluation_output(
        case,
        output,
        "single_prompt_agent",
    ).score == 100


def test_composite_score_requires_both_inputs():
    assert composite_score(80, 90) == 84
    assert composite_score(None, 90) is None
    assert composite_score(80, None) is None


def test_missing_model_price_is_reported_not_zero(tmp_path: Path):
    pricing_path = tmp_path / "pricing.json"
    pricing_path.write_text(
        json.dumps(
            {
                "currency": "USD",
                "unit": "per_1m_tokens",
                "updated_at": "2026-09-14",
                "models": {
                    "gpt-test": {
                        "input": None,
                        "output": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    pricing = load_model_pricing(pricing_path)

    result = estimate_token_cost(
        {
            "gpt-test": {
                "input_tokens": 100,
                "output_tokens": 20,
            }
        },
        pricing,
    )

    assert result["available"] is False
    assert result["total"] is None
    assert result["unavailable_models"] == ["gpt-test"]


def test_estimates_cached_and_uncached_token_costs():
    pricing = {
        "currency": "USD",
        "unit": "per_1m_tokens",
        "updated_at": "2026-09-15",
        "models": {
            "gpt-test": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 15,
            }
        },
    }

    result = estimate_token_cost(
        {
            "gpt-test": {
                "prompt_tokens": 1_000_000,
                "cached_tokens": 200_000,
                "completion_tokens": 100_000,
            }
        },
        pricing,
    )

    assert result["available"] is True
    assert result["total"] == 3.55


def test_cost_ignores_agent_target_alias_when_priced_model_is_present():
    pricing = {
        "currency": "USD",
        "unit": "per_1m_tokens",
        "updated_at": "2026-09-15",
        "models": {
            "gpt-5.4": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 15,
            }
        },
    }

    result = estimate_token_cost(
        {
            "azure_ai_agent_target": {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 1_000_000,
            },
            "gpt-5.4": {
                "prompt_tokens": 1_000_000,
                "cached_tokens": 200_000,
                "completion_tokens": 100_000,
            },
        },
        pricing,
    )

    assert result["available"] is True
    assert result["total"] == 3.55
    assert result["ignored_model_aliases"] == ["azure_ai_agent_target"]
    assert set(result["breakdown"]) == {"gpt-5.4"}


def test_cost_still_reports_unknown_models_with_agent_target_alias():
    pricing = {
        "currency": "USD",
        "unit": "per_1m_tokens",
        "updated_at": "2026-09-15",
        "models": {
            "gpt-5.4": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 15,
            }
        },
    }

    result = estimate_token_cost(
        {
            "azure_ai_agent_target": {"prompt_tokens": 100},
            "unrecognized-model": {"prompt_tokens": 100},
        },
        pricing,
    )

    assert result["available"] is False
    assert result["total"] is None
    assert result["unavailable_models"] == ["unrecognized-model"]
    assert result["ignored_model_aliases"] == ["azure_ai_agent_target"]


def test_agent_target_alias_alone_is_not_reported_as_zero_cost():
    pricing = {
        "currency": "USD",
        "unit": "per_1m_tokens",
        "updated_at": "2026-09-15",
        "models": {
            "gpt-5.4": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 15,
            }
        },
    }

    result = estimate_token_cost(
        {
            "azure_ai_agent_target": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
            }
        },
        pricing,
    )

    assert result["available"] is False
    assert result["total"] is None
    assert result["reason"] == "billable_model_usage_unavailable"


def test_clarification_rejects_hallucinated_empty_field_and_payload():
    case = _case(
        expected_status="needs_clarification",
        expected_request={
            "departure": "東京",
            "destination": "",
            "schedule": "2026年10月20日",
            "purpose": "顧客会議",
        },
        expected_policy_compliant=None,
        expected_clarification_fields=["目的地"],
    )
    output = _draft_output()
    output.update(
        {
            "status": "needs_clarification",
            "request": {
                "departure": "東京",
                "destination": "大阪",
                "schedule": "2026年10月20日",
                "purpose": "顧客会議",
            },
            "clarification_questions": ["目的地を教えてください。"],
            "policy": {"compliant": None, "details": [], "narrative": ""},
            "diagnostics": {
                "schema_version": "1",
                "scenario": "single_prompt_agent",
                "case_id": "case-001",
                "agent_versions": {},
            },
        }
    )

    result = score_evaluation_output(
        case,
        output,
        "single_prompt_agent",
    )

    assert result.score == 60
    assert not next(
        check for check in result.checks if check.name == "request_fields"
    ).passed
    assert not next(
        check for check in result.checks if check.name == "clarification"
    ).passed


def test_diagnostics_must_match_current_case_and_scenario():
    output = _draft_output()
    output["diagnostics"]["case_id"] = "wrong-case"
    output["diagnostics"]["scenario"] = "single_prompt_agent"

    result = score_evaluation_output(
        _case(),
        output,
        "agent_framework_workflow",
    )

    assert result.score == 80
    assert not next(
        check for check in result.checks if check.name == "schema"
    ).passed
