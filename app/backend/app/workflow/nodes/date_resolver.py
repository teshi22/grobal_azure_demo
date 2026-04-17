"""日程の日付解決モジュール

あいまいな日本語日付表現（来週金曜日、明日、4月25日 等）を
具体的な日付に変換する。一意に特定できない場合はあいまい理由を返す。
"""

import re
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

# 曜日マッピング（Python weekday: 月=0 … 日=6）
_WEEKDAY_MAP: dict[str, int] = {
    "月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6,
}
_WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]

# 旅程種別パターン（schedule から分離する）
_TRIP_TYPE_RE = re.compile(r"[、,\s]*(日帰り|\d+泊\d+日)")

# あいまいキーワード（日付部分がこれだけの場合は確定不可）
_AMBIGUOUS_KEYWORDS = frozenset([
    "来週", "来月", "再来月", "今月中", "月末", "月初",
    "近々", "近日中", "そのうち", "週末", "週明け",
    "GW", "ゴールデンウィーク", "お盆", "年末", "年始", "連休",
])

# 日付らしいキーワード（パターン未一致時のフォールバック判定用）
_DATE_LIKE_RE = re.compile(
    r"週|月|曜|明|後|頃|ごろ|くらい|あたり|前半|後半|上旬|中旬|下旬"
)

# 全角→半角変換テーブル
_FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９／－",
    "0123456789/-",
)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    """全角数字・記号を半角に、波ダッシュ系を統一"""
    t = text.translate(_FULLWIDTH_TABLE)
    t = t.replace("～", "〜")  # 全角チルダ → 波ダッシュ
    return t.strip()


def _today_jst(base_date: date | None = None) -> date:
    if base_date:
        return base_date
    return datetime.now(JST).date()


def _fmt(d: date) -> str:
    """'2026-04-24（金）' 形式"""
    return f"{d.isoformat()}（{_WEEKDAY_NAMES[d.weekday()]}）"


def _weekday_of(base: date, weekday: int, weeks_ahead: int) -> date:
    """base の週の月曜を起点に weeks_ahead 週先の指定曜日を返す"""
    monday = base - timedelta(days=base.weekday())
    return monday + timedelta(weeks=weeks_ahead, days=weekday)


def _nearest_future_date(month: int, day: int, base: date) -> date | None:
    """年省略の月日 → 直近の未来日"""
    for year in (base.year, base.year + 1):
        try:
            d = date(year, month, day)
            if d >= base:
                return d
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 単一日付の解決
# ---------------------------------------------------------------------------
def _resolve_single(text: str, base: date) -> date | None:
    """単一の日付テキスト → date。解決不能なら None"""
    t = text.strip()
    if not t:
        return None

    # 今日 / 本日
    if t in ("今日", "本日"):
        return base

    # 明日
    if t == "明日":
        return base + timedelta(days=1)

    # 明後日
    if t in ("明後日", "あさって"):
        return base + timedelta(days=2)

    # 来週X曜(日)
    m = re.match(r"来週の?([月火水木金土日])曜?日?$", t)
    if m:
        return _weekday_of(base, _WEEKDAY_MAP[m.group(1)], 1)

    # 再来週X曜(日)
    m = re.match(r"再来週の?([月火水木金土日])曜?日?$", t)
    if m:
        return _weekday_of(base, _WEEKDAY_MAP[m.group(1)], 2)

    # 今週X曜(日)
    m = re.match(r"今週の?([月火水木金土日])曜?日?$", t)
    if m:
        return _weekday_of(base, _WEEKDAY_MAP[m.group(1)], 0)

    # 次のX曜(日) — 今日より先の直近
    m = re.match(r"次の([月火水木金土日])曜?日?$", t)
    if m:
        wd = _WEEKDAY_MAP[m.group(1)]
        for i in range(1, 8):
            cand = base + timedelta(days=i)
            if cand.weekday() == wd:
                return cand

    # YYYY-MM-DD / YYYY/MM/DD（既に具体的）
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # X月Y日
    m = re.match(r"(\d{1,2})月(\d{1,2})日?$", t)
    if m:
        return _nearest_future_date(int(m.group(1)), int(m.group(2)), base)

    # X/Y
    m = re.match(r"(\d{1,2})/(\d{1,2})$", t)
    if m:
        return _nearest_future_date(int(m.group(1)), int(m.group(2)), base)

    return None


# ---------------------------------------------------------------------------
# 日付テキスト解決（範囲含む）
# ---------------------------------------------------------------------------
def _resolve_date_text(
    date_text: str,
    base_date: date | None = None,
) -> tuple[str | None, str | None]:
    """日付テキストを解決する。

    Returns:
        (resolved_str, None)  — 解決成功
        (None, question)      — あいまい（ユーザーに質問が必要）
        (None, None)          — 日付表現ではない（そのまま通す）
    """
    base = _today_jst(base_date)
    text = _normalize(date_text)
    if not text:
        return None, None

    # あいまいキーワード完全一致
    cleaned = text.rstrip("にの")
    if cleaned in _AMBIGUOUS_KEYWORDS:
        return (
            None,
            f"「{date_text}」の具体的な日付を教えてください"
            "（例: 来週金曜日、4月25日 など）",
        )

    # 範囲表現: X〜Y, X～Y, X-Y, XからY（ただし「から+泊日」は除外済み）
    range_m = re.match(r"(.+?)\s*[〜\-からー]\s*(.+)", text)
    if range_m:
        start_t, end_t = range_m.group(1).strip(), range_m.group(2).strip()
        start_d = _resolve_single(start_t, base)
        if start_d is None:
            return (
                None,
                f"「{date_text}」の具体的な日付を教えてください"
                "（例: 4月25日〜27日 など）",
            )

        end_d = _resolve_single(end_t, base)
        if end_d is None:
            # 曜日のみ省略補完（例: "水曜日" → 開始日と同じ週の水曜日）
            wd_m = re.match(r"([月火水木金土日])曜?日?$", end_t)
            if wd_m and start_d:
                wd = _WEEKDAY_MAP[wd_m.group(1)]
                # 開始日の週の月曜を起点に
                start_monday = start_d - timedelta(days=start_d.weekday())
                end_d = start_monday + timedelta(days=wd)

            # 日のみ省略補完（例: "27日", "27"）
            if end_d is None:
                m_day = re.match(r"(\d{1,2})日?$", end_t)
                if m_day:
                    try:
                        end_day = int(m_day.group(1))
                        end_d = date(start_d.year, start_d.month, end_day)
                        if end_d < start_d:
                            if start_d.month == 12:
                                end_d = date(start_d.year + 1, 1, end_day)
                            else:
                                end_d = date(start_d.year, start_d.month + 1, end_day)
                    except ValueError:
                        pass

        if end_d is None:
            return None, f"「{date_text}」の正確な日付範囲を教えてください"
        if end_d < start_d:
            return None, "終了日が開始日より前になっています。正しい日程を教えてください"

        return f"{_fmt(start_d)}〜{_fmt(end_d)}", None

    # 単一日付
    resolved = _resolve_single(text, base)
    if resolved:
        return _fmt(resolved), None

    # パターン未一致だが日付らしい → あいまい扱い（安全側に倒す）
    if _DATE_LIKE_RE.search(text):
        logger.info("Unrecognized date-like expression: '%s'", date_text)
        return (
            None,
            f"「{date_text}」の具体的な日付を教えてください"
            "（例: 来週金曜日、4月25日 など）",
        )

    # 日付表現ではない → そのまま通す
    return None, None


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------
def resolve_schedule(
    fields: dict[str, str],
    base_date: date | None = None,
) -> str | None:
    """schedule フィールドの日付を解決する。

    - fields は in-place で更新される（解決成功時）
    - 戻り値 None: 解決済み or 日付表現なし → 後続処理へ
    - 戻り値 str: あいまい → ユーザーへの質問文
    """
    schedule = fields.get("schedule", "").strip()
    if not schedule:
        return None

    # 旅程種別を分離（日帰り / N泊M日）
    trip_type = ""
    date_part = schedule

    # 「XからN泊M日」パターンを先に検出
    stay_m = re.match(r"(.+?)から(\d+泊\d+日)$", date_part)
    if stay_m:
        date_part = stay_m.group(1).strip()
        trip_type = stay_m.group(2)
    else:
        trip_m = _TRIP_TYPE_RE.search(date_part)
        if trip_m:
            trip_type = trip_m.group(1)
            date_part = _TRIP_TYPE_RE.sub("", date_part).strip()

    if not date_part:
        return None

    resolved, question = _resolve_date_text(date_part, base_date)

    if question:
        return question

    if resolved:
        if trip_type:
            fields["schedule"] = f"{resolved}、{trip_type}"
        else:
            fields["schedule"] = resolved
        logger.info("Schedule resolved: '%s' → '%s'", schedule, fields["schedule"])

    return None
