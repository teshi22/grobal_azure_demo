"""Authenticated APIs for dual-scenario Foundry evaluation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.auth.entra import CurrentUser
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCaseCreate,
    EvaluationCaseImportRequest,
    EvaluationCaseImportResponse,
    EvaluationCaseResult,
    EvaluationCaseUpdate,
    EvaluationDatasetId,
    EvaluationRun,
    EvaluationRunCreate,
    EVALUATION_DATASET_ID,
    HumanReviewUpdate,
)
from app.services.cosmos import (
    StoreConflictError,
    StoreNotFoundError,
    StorePersistenceError,
    get_evaluation_case_store,
    get_evaluation_result_store,
    get_evaluation_run_store,
)
from app.services.evaluation_cases import (
    CaseImportValidationError,
    import_evaluation_cases,
    parse_evaluation_cases_jsonl,
    seed_default_evaluation_cases,
)
from app.services.evaluation_foundry import (
    FoundryOperationError,
    FoundrySdkUnsupportedError,
    get_evaluation_service,
)
from app.services.evaluation_runs import (
    EvaluationRunCoordinator,
    EvaluationSelectionError,
)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def _coordinator() -> EvaluationRunCoordinator:
    return EvaluationRunCoordinator(
        case_store=get_evaluation_case_store(),
        run_store=get_evaluation_run_store(),
        result_store=get_evaluation_result_store(),
        foundry_service=get_evaluation_service(),
    )


@router.get("/cases", response_model=list[EvaluationCase])
async def list_cases(
    current_user: CurrentUser,
    dataset_id: EvaluationDatasetId = EVALUATION_DATASET_ID,
    enabled: bool | None = None,
):
    del current_user
    return await get_evaluation_case_store().list(
        dataset_id,
        enabled=enabled,
    )


@router.post(
    "/cases",
    response_model=EvaluationCase,
    status_code=status.HTTP_201_CREATED,
)
async def create_case(
    body: EvaluationCaseCreate,
    current_user: CurrentUser,
):
    try:
        return await get_evaluation_case_store().create(
            body.model_dump(mode="json"),
            current_user["sub"],
        )
    except StoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/cases/{case_id}", response_model=EvaluationCase)
async def update_case(
    case_id: str,
    body: EvaluationCaseUpdate,
    current_user: CurrentUser,
):
    changes = body.model_dump(mode="json", exclude={"version"})
    try:
        return await get_evaluation_case_store().update(
            case_id,
            EVALUATION_DATASET_ID,
            changes,
            expected_version=body.version,
            updated_by=current_user["sub"],
        )
    except StoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/cases/import",
    response_model=EvaluationCaseImportResponse,
)
async def import_cases(
    body: EvaluationCaseImportRequest,
    current_user: CurrentUser,
):
    try:
        if body.seed_default:
            imported = await seed_default_evaluation_cases(
                get_evaluation_case_store(),
                user_id=current_user["sub"],
                overwrite=body.overwrite,
            )
        else:
            cases = (
                body.cases
                if body.cases
                else parse_evaluation_cases_jsonl(body.jsonl)
            )
            imported = await import_evaluation_cases(
                get_evaluation_case_store(),
                cases,
                user_id=current_user["sub"],
                overwrite=body.overwrite,
            )
    except CaseImportValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "errors": exc.errors},
        ) from exc
    except StoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EvaluationCaseImportResponse.model_validate(imported)


@router.get("/runs", response_model=list[EvaluationRun])
async def list_runs(
    current_user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
):
    del current_user
    return await get_evaluation_run_store().list(limit=limit)


@router.post(
    "/runs",
    response_model=EvaluationRun,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    body: EvaluationRunCreate,
    current_user: CurrentUser,
):
    try:
        return await _coordinator().create_run(
            comparison_id=str(uuid.uuid4()),
            request=body,
            creator_id=current_user["sub"],
        )
    except EvaluationSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FoundrySdkUnsupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except FoundryOperationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except StorePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/runs/{comparison_id}", response_model=EvaluationRun)
async def get_run(
    comparison_id: str,
    current_user: CurrentUser,
):
    del current_user
    return await _sync_or_http_error(comparison_id)


@router.post("/runs/{comparison_id}/sync", response_model=EvaluationRun)
async def sync_run(
    comparison_id: str,
    current_user: CurrentUser,
):
    del current_user
    return await _sync_or_http_error(comparison_id)


async def _sync_or_http_error(comparison_id: str) -> EvaluationRun:
    try:
        return await _coordinator().sync_run(comparison_id)
    except StoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FoundrySdkUnsupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except FoundryOperationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except StorePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/runs/{comparison_id}/results",
    response_model=list[EvaluationCaseResult],
)
async def list_results(
    comparison_id: str,
    current_user: CurrentUser,
):
    del current_user
    coordinator = _coordinator()
    try:
        await coordinator.sync_run(comparison_id)
        return await coordinator.list_results(comparison_id)
    except StoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put(
    "/runs/{comparison_id}/results/{case_id}/human-review",
    response_model=EvaluationCaseResult,
)
async def update_human_review(
    comparison_id: str,
    case_id: str,
    body: HumanReviewUpdate,
    current_user: CurrentUser,
):
    try:
        return await _coordinator().update_human_review(
            comparison_id=comparison_id,
            case_id=case_id,
            request=body,
            reviewer_id=current_user["sub"],
            reviewer_name=str(
                current_user.get("name")
                or current_user.get("email")
                or ""
            ),
        )
    except StoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
