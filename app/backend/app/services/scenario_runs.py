"""Direct, side-effect-free execution of the two travel-agent scenarios."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from pydantic import ValidationError

from app.config import settings
from app.schemas.evaluation import (
    CanonicalEvaluationOutput,
    EvaluationScenario,
)
from app.schemas.scenario import ScenarioRunResponse
from app.services.evaluation_scoring import parse_canonical_output
from app.services.foundry import (
    invoke_scenario_agent,
    response_to_dict,
)

logger = logging.getLogger(__name__)


class ScenarioExecutionError(RuntimeError):
    """Raised when a direct scenario run cannot produce valid output."""


async def run_scenario(
    *,
    scenario: EvaluationScenario,
    request_input: str,
) -> ScenarioRunResponse:
    run_id = str(uuid.uuid4())
    agent_name, agent_version = _scenario_target(scenario)
    started = time.perf_counter()

    if settings.evaluation_mode == "stub":
        output = _stub_output(
            scenario=scenario,
            run_id=run_id,
            request_input=request_input,
        )
        return ScenarioRunResponse(
            run_id=run_id,
            scenario=scenario,
            agent_name=agent_name,
            agent_version=agent_version or "stub",
            execution_mode="stub",
            duration_ms=_duration_ms(started),
            token_usage={"total_tokens": 0},
            output=output,
        )

    envelope = {
        "mode": "evaluation",
        "schema_version": "1",
        "case_id": run_id,
        "input": request_input,
    }
    try:
        response = await asyncio.to_thread(
            invoke_scenario_agent,
            agent_name=agent_name,
            session_id=(
                run_id
                if scenario == "agent_framework_workflow"
                else None
            ),
            envelope=envelope,
        )
        response_data = response_to_dict(response)
        _raise_for_response_error(response_data)
        output_text = str(
            response_data.get("output_text")
            or getattr(response, "output_text", "")
            or ""
        )
        output = parse_canonical_output(output_text)
        _validate_diagnostics(
            output=output,
            scenario=scenario,
            run_id=run_id,
        )
    except ScenarioExecutionError:
        raise
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ScenarioExecutionError(
            "Agent returned an invalid structured response"
        ) from exc
    except Exception as exc:
        logger.exception(
            "Direct scenario execution failed for scenario=%s run_id=%s",
            scenario,
            run_id,
        )
        raise ScenarioExecutionError(
            f"Scenario execution failed: {exc}"
        ) from exc

    return ScenarioRunResponse(
        run_id=run_id,
        scenario=scenario,
        agent_name=agent_name,
        agent_version=agent_version or "active",
        execution_mode="foundry",
        duration_ms=_duration_ms(started),
        token_usage=_normalize_usage(response_data.get("usage")),
        output=output,
    )


def _scenario_target(
    scenario: EvaluationScenario,
) -> tuple[str, str]:
    if scenario == "agent_framework_workflow":
        return settings.hosted_agent_name, settings.hosted_agent_version
    return (
        settings.single_prompt_evaluation_agent_name,
        settings.single_prompt_evaluation_agent_version,
    )


def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _normalize_usage(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _raise_for_response_error(response_data: dict[str, Any]) -> None:
    error = response_data.get("error")
    if error:
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or error)
        else:
            message = str(error)
        raise ScenarioExecutionError(message)

    status = str(response_data.get("status") or "").lower()
    if status in {"cancelled", "failed", "incomplete"}:
        details = response_data.get("incomplete_details")
        message = (
            str(details.get("reason") or details)
            if isinstance(details, dict)
            else str(details or status)
        )
        raise ScenarioExecutionError(
            f"Agent response did not complete: {message}"
        )


def _validate_diagnostics(
    *,
    output: CanonicalEvaluationOutput,
    scenario: EvaluationScenario,
    run_id: str,
) -> None:
    if output.diagnostics.scenario != scenario:
        raise ScenarioExecutionError(
            "Agent response scenario does not match the requested scenario"
        )
    if output.diagnostics.case_id != run_id:
        raise ScenarioExecutionError(
            "Agent response run identifier does not match the request"
        )


def _stub_output(
    *,
    scenario: EvaluationScenario,
    run_id: str,
    request_input: str,
) -> CanonicalEvaluationOutput:
    agent_versions = (
        {"hosted": "stub"}
        if scenario == "agent_framework_workflow"
        else {"single": "stub"}
    )
    return CanonicalEvaluationOutput.model_validate(
        {
            "status": "draft_ready",
            "request": {
                "departure": "東京",
                "destination": "大阪",
                "schedule": "入力内容を参照",
                "purpose": request_input,
            },
            "clarification_questions": [],
            "itinerary": {
                "departure": "東京",
                "destination": "大阪",
                "purpose": request_input,
                "schedule": "入力内容を参照",
                "trip_type": "日帰り",
                "transportation_legs": [
                    {
                        "direction": "往路",
                        "method": "新幹線",
                        "from": "東京駅",
                        "to": "新大阪駅",
                        "cost": 13870,
                        "fare_type": "指定席",
                        "source_url": "https://railway.jr-central.co.jp/",
                        "source_title": "JR東海",
                    }
                ],
                "transportation_cost": 13870,
                "total_cost": 13870,
            },
            "fare_total": 13870,
            "policy": {
                "compliant": True,
                "details": ["ローカルスタブの規程判定です。"],
                "narrative": "実際のFoundry Agentは呼び出していません。",
            },
            "application_draft": (
                "ローカルスタブで生成した出張申請案です。"
                f"\n目的: {request_input}"
            ),
            "citations": [
                {
                    "url": "https://railway.jr-central.co.jp/",
                    "title": "JR東海",
                    "fare_type": "指定席",
                }
            ],
            "diagnostics": {
                "schema_version": "1",
                "scenario": scenario,
                "case_id": run_id,
                "agent_versions": agent_versions,
            },
        }
    )
