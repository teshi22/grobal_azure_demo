from datetime import date

from travel_agent.date_resolver import resolve_schedule


def test_resolves_relative_weekday():
    fields = {"schedule": "来週金曜日", "departure": "", "destination": "", "purpose": ""}
    question = resolve_schedule(fields, base_date=date(2026, 9, 7))
    assert question is None
    assert fields["schedule"] == "2026-09-18（金）"


def test_requests_concrete_ambiguous_date():
    fields = {"schedule": "来週", "departure": "", "destination": "", "purpose": ""}
    question = resolve_schedule(fields, base_date=date(2026, 9, 7))
    assert question is not None


def test_accepts_iso_date_without_treating_hyphens_as_range():
    fields = {"schedule": "2026-09-10", "departure": "", "destination": "", "purpose": ""}
    question = resolve_schedule(fields, base_date=date(2026, 9, 7))
    assert question is None
    assert fields["schedule"] == "2026-09-10（木）"


def test_accepts_already_formatted_iso_date():
    fields = {
        "schedule": "2026-09-10（木）",
        "departure": "",
        "destination": "",
        "purpose": "",
    }
    question = resolve_schedule(fields, base_date=date(2026, 9, 7))
    assert question is None
    assert fields["schedule"] == "2026-09-10（木）"


def test_accepts_japanese_date_with_day_trip_suffix():
    fields = {
        "schedule": "2026年10月15日の日帰り",
        "departure": "",
        "destination": "",
        "purpose": "",
    }
    question = resolve_schedule(fields, base_date=date(2026, 9, 7))
    assert question is None
    assert fields["schedule"] == "2026-10-15（木）、日帰り"
