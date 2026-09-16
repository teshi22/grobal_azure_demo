import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import settings
from app.schemas.evaluation import (
    EvaluationCaseCreate,
    EvaluationRunCreate,
    MAX_EVALUATION_CASES_PER_RUN,
    MAX_EVALUATION_INPUT_LENGTH,
)
from app.services.evaluation_cases import (
    CaseImportValidationError,
    import_evaluation_cases,
    load_seed_evaluation_cases,
    parse_evaluation_cases_jsonl,
)


class _CaseStore:
    def __init__(self):
        self.items = {}

    async def get(self, case_id, dataset_id):
        return self.items.get((dataset_id, case_id))

    async def create(self, case, creator_id):
        saved = {
            **case,
            "version": 1,
            "created_by": creator_id,
        }
        self.items[(case["dataset_id"], case["id"])] = saved
        return saved

    async def update(
        self,
        case_id,
        dataset_id,
        changes,
        *,
        expected_version,
        updated_by,
    ):
        saved = {
            **self.items[(dataset_id, case_id)],
            **changes,
            "version": expected_version + 1,
            "updated_by": updated_by,
        }
        self.items[(dataset_id, case_id)] = saved
        return saved


def _line(title="Valid"):
    return (
        '{"id":"case-valid","dataset_id":"travel-request-v1",'
        f'"title":"{title}","input":"Tokyo to Osaka",'
        '"expected_status":"draft_ready","expected_request":{},'
        '"tags":[],"enabled":true}'
    )


def test_jsonl_import_reports_invalid_line_numbers():
    content = _line() + "\n{not-json}\n" + '{"id":"bad"}'

    with pytest.raises(CaseImportValidationError) as error:
        parse_evaluation_cases_jsonl(content)

    assert [item["line"] for item in error.value.errors] == [2, 3]


def test_import_is_idempotent_and_can_overwrite():
    store = _CaseStore()
    cases = parse_evaluation_cases_jsonl(_line())

    first = asyncio.run(
        import_evaluation_cases(store, cases, user_id="user-1")
    )
    second = asyncio.run(
        import_evaluation_cases(store, cases, user_id="user-1")
    )
    changed = parse_evaluation_cases_jsonl(_line(title="Changed"))
    third = asyncio.run(
        import_evaluation_cases(
            store,
            changed,
            user_id="user-2",
            overwrite=True,
        )
    )

    assert first["created"] == 1
    assert second["skipped"] == 1
    assert third["updated"] == 1
    assert third["cases"][0]["title"] == "Changed"
    assert third["cases"][0]["updated_by"] == "user-2"


def test_backend_seed_copy_is_valid_and_self_contained():
    cases = load_seed_evaluation_cases()

    assert len(cases) == 20
    assert Path(__file__).parents[1] in Path(settings.evaluation_seed_path).parents


def test_only_travel_request_dataset_is_accepted():
    invalid = _line().replace("travel-request-v1", "other-dataset")

    with pytest.raises(CaseImportValidationError) as error:
        parse_evaluation_cases_jsonl(invalid)

    assert error.value.errors[0]["line"] == 1
    with pytest.raises(ValidationError):
        EvaluationRunCreate(dataset_id="other-dataset")


def test_case_and_run_size_limits_are_enforced():
    with pytest.raises(ValidationError):
        EvaluationCaseCreate(
            id="case-too-large",
            title="Too large",
            input="x" * (MAX_EVALUATION_INPUT_LENGTH + 1),
            expected_status="draft_ready",
        )
    with pytest.raises(ValidationError):
        EvaluationRunCreate(
            case_ids=[
                f"case-{index:03d}"
                for index in range(MAX_EVALUATION_CASES_PER_RUN + 1)
            ]
        )
