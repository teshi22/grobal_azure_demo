"""Resolve common Japanese date expressions to concrete JST dates."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_WEEKDAY_MAP = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
_WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]
_TRIP_TYPE_RE = re.compile(r"[、,\s]*(日帰り|\d+泊\d+日)")
_AMBIGUOUS = frozenset(
    {
        "来週",
        "来月",
        "再来月",
        "今月中",
        "月末",
        "月初",
        "近々",
        "近日中",
        "週末",
        "週明け",
        "GW",
        "ゴールデンウィーク",
        "お盆",
        "年末",
        "年始",
        "連休",
    }
)
_DATE_LIKE_RE = re.compile(r"週|月|曜|明|後|頃|ごろ|上旬|中旬|下旬")
_FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９／－", "0123456789/-"
)


def _normalize(text: str) -> str:
    return text.translate(_FULLWIDTH_TABLE).replace("～", "〜").strip()


def _fmt(value: date) -> str:
    return f"{value.isoformat()}（{_WEEKDAY_NAMES[value.weekday()]}）"


def _weekday_of(base: date, weekday: int, weeks_ahead: int) -> date:
    monday = base - timedelta(days=base.weekday())
    return monday + timedelta(weeks=weeks_ahead, days=weekday)


def _nearest_future_date(month: int, day: int, base: date) -> date | None:
    for year in (base.year, base.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= base:
            return candidate
    return None


def _resolve_single(text: str, base: date) -> date | None:
    value = re.sub(r"[（(][月火水木金土日][）)]$", "", text.strip())
    if value in ("今日", "本日"):
        return base
    if value == "明日":
        return base + timedelta(days=1)
    if value in ("明後日", "あさって"):
        return base + timedelta(days=2)

    relative = re.fullmatch(r"(今週|来週|再来週)の?([月火水木金土日])曜?日?", value)
    if relative:
        offset = {"今週": 0, "来週": 1, "再来週": 2}[relative.group(1)]
        return _weekday_of(base, _WEEKDAY_MAP[relative.group(2)], offset)

    next_weekday = re.fullmatch(r"次の([月火水木金土日])曜?日?", value)
    if next_weekday:
        weekday = _WEEKDAY_MAP[next_weekday.group(1)]
        for offset in range(1, 8):
            candidate = base + timedelta(days=offset)
            if candidate.weekday() == weekday:
                return candidate

    iso_date = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if iso_date:
        try:
            return date(*(int(part) for part in iso_date.groups()))
        except ValueError:
            return None

    japanese_date = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", value)
    if japanese_date:
        try:
            return date(*(int(part) for part in japanese_date.groups()))
        except ValueError:
            return None

    month_day = re.fullmatch(r"(\d{1,2})月(\d{1,2})日?", value)
    if not month_day:
        month_day = re.fullmatch(r"(\d{1,2})/(\d{1,2})", value)
    if month_day:
        return _nearest_future_date(
            int(month_day.group(1)), int(month_day.group(2)), base
        )
    return None


def _resolve_date_text(
    text: str, base_date: date | None = None
) -> tuple[str | None, str | None]:
    base = base_date or datetime.now(JST).date()
    value = _normalize(text)
    if not value:
        return None, None
    if value.rstrip("にの") in _AMBIGUOUS:
        return None, f"「{text}」の具体的な日付を教えてください。"

    resolved = _resolve_single(value, base)
    if resolved:
        return _fmt(resolved), None

    date_range = re.fullmatch(r"(.+?)\s*(?:〜|-|から|ー)\s*(.+?)(?:まで)?", value)
    if date_range:
        start_text, end_text = date_range.groups()
        start = _resolve_single(start_text, base)
        if start is None:
            return None, f"「{text}」の開始日を具体的に教えてください。"
        end = _resolve_single(end_text, base)
        if end is None:
            day_only = re.fullmatch(r"(\d{1,2})日?", end_text.strip())
            if day_only:
                try:
                    end = date(start.year, start.month, int(day_only.group(1)))
                    if end < start:
                        next_month = start.replace(day=28) + timedelta(days=4)
                        end = date(
                            next_month.year,
                            next_month.month,
                            int(day_only.group(1)),
                        )
                except ValueError:
                    end = None
        if end is None:
            return None, f"「{text}」の終了日を具体的に教えてください。"
        if end < start:
            return None, "終了日が開始日より前です。正しい日程を教えてください。"
        return f"{_fmt(start)}〜{_fmt(end)}", None

    if _DATE_LIKE_RE.search(value):
        return None, f"「{text}」の具体的な日付を教えてください。"
    return None, None


def resolve_schedule(
    fields: dict[str, str], base_date: date | None = None
) -> str | None:
    schedule = fields.get("schedule", "").strip()
    if not schedule:
        return None

    trip_type = ""
    date_part = schedule
    stay = re.fullmatch(r"(.+?)から(\d+泊\d+日)", date_part)
    if stay:
        date_part, trip_type = stay.groups()
    else:
        match = _TRIP_TYPE_RE.search(date_part)
        if match:
            trip_type = match.group(1)
            date_part = _TRIP_TYPE_RE.sub("", date_part).rstrip("、, の")

    resolved, question = _resolve_date_text(date_part, base_date)
    if question:
        return question
    if resolved:
        fields["schedule"] = f"{resolved}、{trip_type}" if trip_type else resolved
    return None
