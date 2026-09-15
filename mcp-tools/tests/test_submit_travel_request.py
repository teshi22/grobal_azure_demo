import hashlib
import json

from tools.submit_travel_request import _plan_hash, _validate_arguments


def test_plan_hash_uses_compact_utf8_json():
    plan = {"departure": "大阪", "destination": "東京"}
    expected = hashlib.sha256(
        json.dumps(
            plan,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert _plan_hash(plan) == expected


def test_submission_requires_grant_and_structured_plan():
    error = _validate_arguments(
        {
            "application_text": "申請書",
            "conversation_id": "conversation-1",
        }
    )
    assert error == "approval_grant_id is required"
