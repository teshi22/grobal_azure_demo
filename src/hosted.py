# =============================================================================
# 出張申請エージェント - ホステッドエージェント版 (Human-in-the-Loop 対応)
# =============================================================================
# Agent Framework ワークフローを Foundry Agent Service のホステッドエージェントとして
# デプロイするためのエントリーポイント。
#
# HITL ポイント:
#   - 情報確認: RequestClarifier が情報不足と判定 → ユーザーに追加質問
#   - プラン確認: TravelPlanner の結果をユーザーに提示 → 確定 or 変更要望
#     ctx.request_info() でワークフロー停止、FileCheckpointRepository で状態永続化
#     ユーザーが次メッセージを送ると @response_handler で再開
#
# 会話フロー:
#   1. ユーザー: 出張リクエスト → RequestClarifier → (不足あれば HITL で質問)
#   2. 情報が揃ったら → TravelPlanner → プラン HITL
#   3. ユーザー: OK or 変更要望 → (変更なら TravelPlanner へ戻る)
#      OK なら PolicyChecker → ApprovalAgent → 申請書出力
#
# ローカルテスト:
#   python src/hosted.py
#   → http://localhost:8088/responses にPOSTでリクエスト
# =============================================================================

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from agent_framework import (
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from azure.ai.agentserver.agentframework import from_agent_framework
from azure.ai.agentserver.agentframework.persistence import (
    FileCheckpointRepository,
    FoundryCheckpointRepository,
)
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
REQUEST_CLARIFIER_AGENT = os.environ.get("REQUEST_CLARIFIER_AGENT", "RequestClarifier")
TRAVEL_PLANNER_AGENT = os.environ.get("TRAVEL_PLANNER_AGENT", "TravelPlanner")
POLICY_CHECKER_AGENT = os.environ.get("POLICY_CHECKER_AGENT", "PolicyChecker")
APPROVAL_AGENT_NAME = os.environ.get("APPROVAL_AGENT", "ApprovalAgent")

_credential = DefaultAzureCredential()
_async_credential = AsyncDefaultAzureCredential()
_token_provider = get_bearer_token_provider(
    _credential, "https://cognitiveservices.azure.com/.default"
)
_project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=_credential)
_openai_client = _project_client.get_openai_client()


# ---------------------------------------------------------------------------
# 旅費規程チェックロジック
# ---------------------------------------------------------------------------
def evaluate_policy(args: dict) -> dict:
    results = []
    ok = True

    hotel = args.get("hotel_cost_per_night", 0)
    transport = args.get("transportation", "")
    distance = args.get("distance_km", 0)
    travel_time = args.get("travel_time_hours", 0)

    limit = 12000
    if hotel > limit:
        results.append(f"❌ 宿泊費 ¥{hotel:,} は上限 ¥{limit:,} を超過")
        ok = False
    elif hotel > 0:
        results.append(f"✅ 宿泊費 ¥{hotel:,} は上限 ¥{limit:,} 以内")

    if "グリーン" in transport:
        if travel_time >= 3:
            results.append("✅ グリーン車: 乗車時間3時間超のため利用可")
        else:
            results.append("❌ グリーン車: 利用条件を満たしていません")
            ok = False

    if "飛行機" in transport or "航空" in transport:
        if distance >= 600:
            results.append(f"✅ 航空機: 片道 {distance}km ≥ 600km のため利用可")
        else:
            results.append(f"⚠️ 航空機: 片道 {distance}km < 600km のため要確認")

    if args.get("needs_pre_night_stay"):
        results.append("✅ 前泊: 始業時刻に間に合わない場合として申請")

    if not results:
        results.append("✅ 規程上の問題は見つかりませんでした")

    return {"compliant": ok, "details": results}


# ---------------------------------------------------------------------------
# Pydantic モデル（ワークフロー内ルーティング用）
# ---------------------------------------------------------------------------
class PolicyCheckResult(BaseModel):
    compliant: bool = Field(description="規程に適合しているか")
    details: list[str] = Field(description="チェック結果の詳細")
    policy_text: str = Field(description="PolicyChecker の応答テキスト")


class ClarificationResult(BaseModel):
    """RequestClarifier の判定結果"""
    complete: bool = Field(description="必要情報が揃っているか")
    enriched_request: str = Field(default="", description="整理済みリクエスト")
    missing_fields: list[str] = Field(default_factory=list, description="不足フィールド")
    question: str = Field(default="", description="ユーザーへの質問")


# ---------------------------------------------------------------------------
# HITL データクラス
# ---------------------------------------------------------------------------
@dataclass
class ClarificationHITLRequest:
    """情報不足時のユーザー質問 HITL リクエスト"""
    question: str
    missing_fields: list[str]
    original_input: str

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
            pass
        return ClarificationHITLResponse(answer=text)
@dataclass
class PlanReviewRequest:
    """プラン確認の HITL リクエスト"""
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
            return f"{self.plan_text}\n\nこのプランでよろしいですか？（OK / 変更要望を入力）"


@dataclass
class PlanReviewResponse:
    """プラン確認の HITL レスポンス"""
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
            pass
        return PlanReviewResponse(approved=False, feedback=text)


@dataclass
class SubmissionRequest:
    """申請確認リクエスト（将来の HITL 拡張用に保持）"""
    application_text: str

    def convert_to_payload(self) -> str:
        return (
            f"{self.application_text}\n\n"
            "この内容で申請システムへ送信しますか？\n"
            "→ 「送信」で確定 / 「取消」でキャンセル"
        )


@dataclass
class SubmissionResponse:
    """申請確認レスポンス（将来の HITL 拡張用に保持）"""
    confirmed: bool

    @staticmethod
    def convert_from_payload(payload: str) -> "SubmissionResponse":
        text = payload.strip().lower()
        confirmed = text in ("送信", "はい", "yes", "ok", "y", "送る", "確定")
        return SubmissionResponse(confirmed=confirmed)


# ---------------------------------------------------------------------------
# Foundry Agent ラッパー Executor
# ---------------------------------------------------------------------------
class FoundryAgentNode(Executor):
    def __init__(self, id, agent_name, function_handler=None):
        super().__init__(id=id)
        self.agent_name = agent_name
        self.function_handler = function_handler

    @handler(input=str, output=str)
    async def invoke(self, input_text, ctx) -> None:
        text, func_result = await asyncio.to_thread(self._call_agent, input_text)
        ctx.set_state(f"{self.id}_response", text)
        if func_result is not None:
            ctx.set_state(f"{self.id}_function_result", func_result)
        await ctx.send_message(text)

    def _call_agent(self, input_text: str) -> tuple[str, dict | None]:
        func_result = None
        conv = _openai_client.conversations.create(
            items=[{"type": "message", "role": "user", "content": input_text}],
        )
        response = _openai_client.responses.create(
            conversation=conv.id,
            extra_body={"agent_reference": {"name": self.agent_name, "type": "agent_reference"}},
        )
        if self.function_handler:
            for output in response.output:
                if output.type == "function_call":
                    args = json.loads(output.arguments)
                    func_result = self.function_handler(args)
                    _openai_client.conversations.items.create(
                        conversation_id=conv.id,
                        items=[{
                            "type": "function_call_output",
                            "call_id": output.call_id,
                            "output": json.dumps(func_result, ensure_ascii=False),
                        }],
                    )
                    response = _openai_client.responses.create(
                        conversation=conv.id,
                        extra_body={"agent_reference": {"name": self.agent_name, "type": "agent_reference"}},
                    )
        text = response.output_text
        try:
            _openai_client.conversations.delete(conversation_id=conv.id)
        except Exception:
            pass
        return text, func_result


# ---------------------------------------------------------------------------
# ワークフロー Executor ノード
# ---------------------------------------------------------------------------
class MessageToStrStep(Executor):
    """list[Message] → str 変換（入口ノード）"""

    def __init__(self):
        super().__init__(id="message_to_str")

    @handler(input=list[Message], output=str)
    async def run(self, messages, ctx) -> None:
        text = " ".join(m.text or "" for m in messages if m.text).strip()
        await ctx.send_message(text)


class RequestClarifierStep(Executor):
    """RequestClarifier Foundry Agent で入力の過不足を判定する。

    complete=True → enriched_request を TravelPlanner へ
    complete=False → HITL でユーザーに質問 → 回答を元に再判定
    """

    def __init__(self):
        super().__init__(id="request_clarifier")

    def _call_clarifier(self, user_input: str) -> dict:
        import re
        conv = _openai_client.conversations.create(
            items=[{"type": "message", "role": "user", "content": user_input}],
        )
        response = _openai_client.responses.create(
            conversation=conv.id,
            extra_body={"agent_reference": {"name": REQUEST_CLARIFIER_AGENT, "type": "agent_reference"}},
        )
        text = response.output_text
        try:
            _openai_client.conversations.delete(conversation_id=conv.id)
        except Exception:
            pass
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        raw = m.group(1) if m else text
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"complete": True, "enriched_request": user_input}

    @handler(input=str, output=ClarificationResult)
    async def run(self, user_input, ctx) -> None:
        result = await asyncio.to_thread(self._call_clarifier, user_input)
        ctx.set_state("original_input", user_input)
        cr = ClarificationResult(
            complete=result.get("complete", True),
            enriched_request=result.get("enriched_request", user_input),
            missing_fields=result.get("missing_fields", []),
            question=result.get("question", ""),
        )
        await ctx.send_message(cr)


def is_clarification_complete(message: Any) -> bool:
    if isinstance(message, ClarificationResult):
        return message.complete
    return True


def is_clarification_incomplete(message: Any) -> bool:
    if isinstance(message, ClarificationResult):
        return not message.complete
    return False


class ClarificationToRequestStep(Executor):
    """ClarificationResult (complete=true) → enriched_request テキストに変換"""

    def __init__(self):
        super().__init__(id="clarification_to_request")

    @handler(input=ClarificationResult, output=str)
    async def run(self, result, ctx) -> None:
        await ctx.send_message(result.enriched_request)


class UserClarificationStep(Executor):
    """ClarificationResult (complete=false) → HITL でユーザーに追加情報を質問"""

    def __init__(self):
        super().__init__(id="user_clarification")

    @handler(input=ClarificationResult, output=str)
    async def handle_incomplete(self, result, ctx) -> None:
        ctx.set_state("clarification_round", ctx.get_state("clarification_round", 0) + 1)
        await ctx.request_info(
            request_data=ClarificationHITLRequest(
                question=result.question or "追加情報を教えてください。",
                missing_fields=result.missing_fields,
                original_input=ctx.get_state("original_input", ""),
            ),
            response_type=ClarificationHITLResponse,
        )

    @response_handler
    async def handle_answer(
        self,
        original: ClarificationHITLRequest,
        response: ClarificationHITLResponse,
        ctx: WorkflowContext,
    ) -> None:
        round_num = ctx.get_state("clarification_round", 1)
        updated = f"{original.original_input}\n{response.answer}"
        ctx.set_state("original_input", updated)
        if round_num >= 3:
            await ctx.send_message(updated, target_id="clarification_to_request_direct")
        else:
            await ctx.send_message(updated, target_id="request_clarifier")


class ClarificationDirectStep(Executor):
    """確認ラウンド上限 → そのまま TravelPlanner へ"""

    def __init__(self):
        super().__init__(id="clarification_to_request_direct")

    @handler(input=str, output=str)
    async def run(self, text, ctx) -> None:
        await ctx.send_message(text)


class PlanReviewStep(Executor):
    """HITL: プラン確認 — ワークフローを停止してユーザーに確認を求める"""

    def __init__(self):
        super().__init__(id="plan_review")

    @handler(input=str, output=str)
    async def handle_plan(self, plan_text, ctx) -> None:
        ctx.set_state("current_plan", plan_text)
        await ctx.request_info(
            request_data=PlanReviewRequest(plan_text=plan_text),
            response_type=PlanReviewResponse,
        )

    @response_handler
    async def handle_review(
        self,
        original: PlanReviewRequest,
        response: PlanReviewResponse,
        ctx: WorkflowContext,
    ) -> None:
        if response.approved:
            await ctx.send_message(original.plan_text, target_id="to_policy_input")
        else:
            revision = (
                f"以下の出張プランを変更してください。\n\n"
                f"【前回のプラン】\n{original.plan_text}\n\n"
                f"【変更要望】\n{response.feedback}"
            )
            await ctx.send_message(revision, target_id="travel_planner")


class ToPolicyInputStep(Executor):
    """確認済みプラン → PolicyChecker 入力に変換"""

    def __init__(self):
        super().__init__(id="to_policy_input")

    @handler(input=str, output=str)
    async def run(self, plan_text, ctx) -> None:
        prompt = f"以下の出張プランを旅費規程に照らしてチェックしてください:\n\n{plan_text}"
        await ctx.send_message(prompt)


class PolicyRouterStep(Executor):
    def __init__(self):
        super().__init__(id="policy_router")

    @handler(input=str, output=PolicyCheckResult)
    async def run(self, policy_text, ctx) -> None:
        func_result = ctx.get_state("policy_checker_function_result")
        if func_result:
            compliant = func_result["compliant"]
            details = func_result["details"]
        else:
            compliant = "❌" not in policy_text
            details = [policy_text]
        result = PolicyCheckResult(compliant=compliant, details=details, policy_text=policy_text)
        await ctx.send_message(result)


def is_compliant(message: Any) -> bool:
    return isinstance(message, PolicyCheckResult) and message.compliant


def is_not_compliant(message: Any) -> bool:
    return isinstance(message, PolicyCheckResult) and not message.compliant


class ToApprovalInputStep(Executor):
    def __init__(self):
        super().__init__(id="to_approval_input")

    @handler(input=PolicyCheckResult, output=str)
    async def run(self, result, ctx) -> None:
        plan_text = ctx.get_state("travel_planner_response", "")
        prompt = (
            f"以下の情報を元に出張申請書を作成してください。\n\n"
            f"【旅程プラン】\n{plan_text}\n\n"
            f"【旅費規程チェック結果】\n{result.policy_text}"
        )
        await ctx.send_message(prompt)


class HandleRejectionStep(Executor):
    """規程不適合 — 差し戻し"""

    def __init__(self):
        super().__init__(id="handle_rejection")

    @handler(input=PolicyCheckResult)
    async def run(self, result, ctx) -> None:
        output = "❌ 旅費規程チェック: 不適合\n\n"
        output += "\n".join(f"  {d}" for d in result.details)
        output += "\n\nプランを修正して再申請してください。"
        await ctx.yield_output(output)


class OutputResultStep(Executor):
    """最終出力: 申請書を整形して出力する"""

    def __init__(self):
        super().__init__(id="output_result")

    @handler(input=str)
    async def run(self, text, ctx) -> None:
        marker = "===== 出張申請書 ====="
        if marker in text:
            text = text[text.index(marker):]
        text += "\n\n✅ 出張申請を申請システムへ送信しました！"
        await ctx.yield_output(text)


# ---------------------------------------------------------------------------
# ワークフロー構築
# ---------------------------------------------------------------------------
def create_builder():
    """HITL 対応ワークフロービルダーを返す。

    グラフ:
      MessageToStr → RequestClarifier ──[complete]──→ ClarificationToRequest → TravelPlanner
                          ↑             [incomplete]
                          |                  ↓
                          └──── UserClarification (HITL: 追加情報)
                                     |  (ラウンド上限)
                                     ↓
                              ClarificationDirect ──→ TravelPlanner

      TravelPlanner → PlanReview (HITL)
                           ↑               |
                           └── (変更要望) ──┘
                                            |  (OK)
                                            ↓
                     ToPolicyInput → PolicyChecker → PolicyRouter
                                                        |
                                               ┌───────┴───────┐
                                              OK               NG
                                               ↓                ↓
                                          ToApproval     HandleRejection
                                               ↓
                                          ApprovalAgent → OutputResult
    """
    message_to_str = MessageToStrStep()
    request_clarifier = RequestClarifierStep()
    clarification_to_request = ClarificationToRequestStep()
    user_clarification = UserClarificationStep()
    clarification_direct = ClarificationDirectStep()
    travel_planner = FoundryAgentNode(id="travel_planner", agent_name=TRAVEL_PLANNER_AGENT)
    plan_review = PlanReviewStep()
    to_policy_input = ToPolicyInputStep()
    policy_checker = FoundryAgentNode(
        id="policy_checker",
        agent_name=POLICY_CHECKER_AGENT,
        function_handler=evaluate_policy,
    )
    policy_router = PolicyRouterStep()
    to_approval_input = ToApprovalInputStep()
    approval_agent = FoundryAgentNode(id="approval_agent", agent_name=APPROVAL_AGENT_NAME)
    handle_rejection = HandleRejectionStep()
    output_result = OutputResultStep()

    builder = (
        WorkflowBuilder(start_executor=message_to_str)
        # RequestClarifier: 入力の過不足を判定
        .add_edge(message_to_str, request_clarifier)
        .add_edge(request_clarifier, clarification_to_request, condition=is_clarification_complete)
        .add_edge(request_clarifier, user_clarification, condition=is_clarification_incomplete)
        .add_edge(user_clarification, request_clarifier)        # 追加情報 → 再判定
        .add_edge(user_clarification, clarification_direct)     # ラウンド上限 → そのまま進む
        .add_edge(clarification_direct, travel_planner)
        .add_edge(clarification_to_request, travel_planner)
        # TravelPlanner → PlanReview (HITL)
        .add_edge(travel_planner, plan_review)
        .add_edge(plan_review, travel_planner)        # 変更要望 → 再検索ループ
        .add_edge(plan_review, to_policy_input)        # OK → 規程チェックへ
        .add_edge(to_policy_input, policy_checker)
        .add_edge(policy_checker, policy_router)
        .add_edge(policy_router, to_approval_input, condition=is_compliant)
        .add_edge(policy_router, handle_rejection, condition=is_not_compliant)
        .add_edge(to_approval_input, approval_agent)
        .add_edge(approval_agent, output_result)
    )
    return builder


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------
async def main():
    builder = create_builder()

    # ホステッド環境では FoundryCheckpointRepository、ローカルでは FileCheckpointRepository
    if os.environ.get("FOUNDRY_HOSTED", "0") == "1":
        checkpoint_repo = FoundryCheckpointRepository(
            project_endpoint=PROJECT_ENDPOINT,
            credential=_async_credential,
        )
    else:
        checkpoint_repo = FileCheckpointRepository(storage_path="./checkpoints")

    await from_agent_framework(
        builder,
        checkpoint_repository=checkpoint_repo,
    ).run_async()


if __name__ == "__main__":
    asyncio.run(main())
