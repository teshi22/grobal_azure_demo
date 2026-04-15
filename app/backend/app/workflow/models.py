"""ワークフロー データモデル

Pydantic モデル (ノード間ルーティング用) + HITL dataclass (停止/再開用)
"""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic モデル (ワークフロー内ルーティング)
# ---------------------------------------------------------------------------
class ExtractedRequest(BaseModel):
    """LLM 構造化出力用 — ユーザー入力から4項目を抽出"""

    departure: str = Field(default="", description="出発地")
    destination: str = Field(default="", description="目的地")
    schedule: str = Field(default="", description="日程")
    purpose: str = Field(default="", description="出張目的")


class ClarificationResult(BaseModel):
    """RequestClarifier の判定結果"""

    complete: bool = Field(description="必要情報が揃っているか")
    enriched_request: str = Field(default="", description="整理済みリクエスト")
    missing_fields: list[str] = Field(default_factory=list, description="不足フィールド")
    question: str = Field(default="", description="ユーザーへの質問")


class PolicyCheckResult(BaseModel):
    """旅費規程チェック結果"""

    compliant: bool = Field(description="規程に適合しているか")
    details: list[str] = Field(description="チェック結果の詳細")
    policy_text: str = Field(description="PolicyChecker の応答テキスト")


# ---------------------------------------------------------------------------
# HITL dataclass (request_info / response_handler 用)
# ---------------------------------------------------------------------------
@dataclass
class ClarificationHITLRequest:
    """情報不足時のユーザー質問リクエスト"""

    question: str
    missing_fields: list[str]
    original_input: str
    extracted_fields: dict | None = None
    departure_is_default: bool = False
    clarification_round: int = 0

    def convert_to_payload(self) -> str:
        lines = ["❓ 情報が不足しています", ""]
        if self.missing_fields:
            lines.append("不足項目: " + "、".join(self.missing_fields))
            lines.append("")
        lines.append(self.question)
        return "\n".join(lines)


@dataclass
class ClarificationHITLResponse:
    """ユーザーの追加情報回答"""

    answer: str

    @staticmethod
    def convert_from_payload(payload: str) -> "ClarificationHITLResponse":
        text = payload.strip()
        try:
            data = json.loads(payload)
            return ClarificationHITLResponse(answer=data.get("answer", text))
        except Exception:
            return ClarificationHITLResponse(answer=text)


@dataclass
class RequestConfirmHITLRequest:
    """整理済みリクエストの確認 HITL リクエスト"""

    enriched_request: str
    departure: str = ""
    destination: str = ""
    schedule: str = ""
    purpose: str = ""

    def convert_to_payload(self) -> str:
        lines = ["📝 以下の内容で旅程を検索します", ""]
        if self.departure:
            lines.append(f"出発地: {self.departure}")
        if self.destination:
            lines.append(f"目的地: {self.destination}")
        if self.schedule:
            lines.append(f"日程: {self.schedule}")
        if self.purpose:
            lines.append(f"目的: {self.purpose}")
        lines.append("")
        lines.append("よろしいですか？ → 「OK」で検索開始 / 修正内容をテキストで入力")
        return "\n".join(lines)


@dataclass
class RequestConfirmHITLResponse:
    """リクエスト確認 HITL レスポンス"""

    confirmed: bool
    revision: str

    @staticmethod
    def convert_from_payload(payload: str) -> "RequestConfirmHITLResponse":
        text = payload.strip()
        if text.lower() in ("ok", "yes", "y", "はい", "確定", "進めて", "大丈夫"):
            return RequestConfirmHITLResponse(confirmed=True, revision="")
        return RequestConfirmHITLResponse(confirmed=False, revision=text)


@dataclass
class PlanReviewRequest:
    """プラン確認 HITL リクエスト"""

    plan_text: str

    def convert_to_payload(self) -> str:
        try:
            plan = json.loads(self.plan_text)
            lines = ["📋 旅程プラン確認", ""]
            trip_type = plan.get("trip_type", "宿泊")
            lines.append(f"種別: {trip_type}")
            for i, leg in enumerate(plan.get("transportation_legs", []), 1):
                lines.append(
                    f"  {i}. {leg.get('method', '—')}  "
                    f"{leg.get('from', '—')} → {leg.get('to', '—')}  "
                    f"¥{leg.get('cost', 0):,}"
                )
            lines.append(f"交通費計: ¥{plan.get('transportation_cost', 0):,}")
            if trip_type == "宿泊":
                nights = plan.get("hotel_nights", 1)
                lines.append(
                    f"宿泊先: {plan.get('hotel', '—')} "
                    f"¥{plan.get('hotel_cost_per_night', 0):,}/泊 × {nights}泊"
                )
            else:
                lines.append("宿泊: なし（日帰り）")
            lines.append(f"合計: ¥{plan.get('total_cost', 0):,}")
            lines.append("")
            lines.append("このプランでよろしいですか？")
            lines.append("→ 「OK」で確定 / 変更要望をテキストで入力")
            return "\n".join(lines)
        except Exception:
            return f"{self.plan_text}\n\nこのプランでよろしいですか？"


@dataclass
class PlanReviewResponse:
    """プラン確認 HITL レスポンス"""

    approved: bool
    feedback: str

    @staticmethod
    def convert_from_payload(payload: str) -> "PlanReviewResponse":
        text = payload.strip()
        if text.lower() in ("ok", "yes", "y", "はい", "確定", "進めて", "大丈夫"):
            return PlanReviewResponse(approved=True, feedback="")
        try:
            data = json.loads(payload)
            return PlanReviewResponse(
                approved=data.get("approved", False),
                feedback=data.get("feedback", ""),
            )
        except Exception:
            return PlanReviewResponse(approved=False, feedback=text)
