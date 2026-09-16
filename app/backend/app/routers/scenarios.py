"""Authenticated APIs for directly trying either agent scenario."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.auth.entra import CurrentUser
from app.schemas.scenario import ScenarioRunRequest, ScenarioRunResponse
from app.services.scenario_runs import (
    ScenarioExecutionError,
    run_scenario,
)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.post("/run", response_model=ScenarioRunResponse)
async def create_scenario_run(
    body: ScenarioRunRequest,
    current_user: CurrentUser,
):
    del current_user
    try:
        return await run_scenario(
            scenario=body.scenario,
            request_input=body.input,
        )
    except ScenarioExecutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
