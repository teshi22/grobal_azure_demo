# =============================================================================
# 出張申請エージェント - Agent Framework + Foundry Agent Service ハイブリッド版
# =============================================================================
# ワークフロー管理:  Microsoft Agent Framework (WorkflowBuilder)
# 個別エージェント:  Foundry Agent Service (Responses API + サーバー側ツール)
#
# ワークフローグラフ:
#   [Foundry: TravelPlanner + BingGrounding]
#          ↓ str (旅程テキスト)
#   [Transform: to_policy_input]
#          ↓ str (チェック依頼プロンプト)
#   [Foundry: PolicyChecker + FunctionTool]
#          ↓ str (チェック結果テキスト)
#   [PolicyRouter] ← shared state から compliant を取得
#          ↓ PolicyCheckResult
#      ┌───┴───┐
#    [OK]    [NG]         ← 条件分岐エッジ
#      ↓       ↓
#  [Transform] [Rejection]
#      ↓
#   [Foundry: ApprovalAgent]
#      ↓
#   [Output]
# =============================================================================

import asyncio
import json
import os
from typing import Any

from agent_framework import (
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    executor,
    handler,
)
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from dotenv import load_dotenv
from opentelemetry import trace
from pydantic import BaseModel, Field
from typing_extensions import Never

load_dotenv()

# ---------------------------------------------------------------------------
# Azure Monitor トレース設定
# ---------------------------------------------------------------------------
APPINSIGHTS_CONN = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
if APPINSIGHTS_CONN:
    configure_azure_monitor(connection_string=APPINSIGHTS_CONN)

tracer = trace.get_tracer("travel-agent-workflow")

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
MODEL = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
BING_CONNECTION_ID = os.environ.get("BING_PROJECT_CONNECTION_ID", "")

# Foundry Agent 名（build_agents.py で作成済み → .env に保存）
REQUEST_CLARIFIER_AGENT = os.environ.get("REQUEST_CLARIFIER_AGENT", "RequestClarifier")
TRAVEL_PLANNER_AGENT = os.environ.get("TRAVEL_PLANNER_AGENT", "TravelPlanner")
POLICY_CHECKER_AGENT = os.environ.get("POLICY_CHECKER_AGENT", "PolicyChecker")
APPROVAL_AGENT_NAME = os.environ.get("APPROVAL_AGENT", "ApprovalAgent")

# ---------------------------------------------------------------------------
# 社内旅費規程
# ---------------------------------------------------------------------------
TRAVEL_POLICY = """
【社内旅費規程】
1. 宿泊費上限: 12,000円/泊
2. 交通手段: 新幹線普通車・指定席を原則とする
3. グリーン車: 乗車時間3時間超の場合に限り利用可
4. 航空機利用: 片道600km以上、または新幹線より安価な場合に利用可
5. 前泊: 始業時刻(9:00)に間に合わない場合に認められる
6. 日当: 国内出張 2,500円/日、海外出張 5,000円/日
7. タクシー: 原則禁止。深夜・早朝(22:00〜6:00)または荷物が多い場合のみ可
"""

# ---------------------------------------------------------------------------
# Foundry Agent ツール定義（BingGrounding は workflow 側では不要だが参考用に残す）
# ---------------------------------------------------------------------------
# ツール定義・エージェント指示は build_agents.py に集約。
# workflow.py では FunctionTool のハンドラ（evaluate_policy）のみ必要。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 旅費規程チェックロジック（FunctionTool のハンドラ）
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
# Pydantic モデル（ワークフロー内のルーティング用）
# ---------------------------------------------------------------------------


class ClarificationResult(BaseModel):
    """RequestClarifier の判定結果"""
    complete: bool = Field(description="必要情報が揃っているか")
    enriched_request: str = Field(default="", description="情報が十分な場合の整理済みリクエスト")
    missing_fields: list[str] = Field(default_factory=list, description="不足フィールド")
    question: str = Field(default="", description="ユーザーへの質問")
    conversation: str = Field(default="", description="これまでの会話履歴")


class PolicyCheckResult(BaseModel):
    compliant: bool = Field(description="規程に適合しているか")
    details: list[str] = Field(description="チェック結果の詳細")
    policy_text: str = Field(description="PolicyChecker の応答テキスト")


# ---------------------------------------------------------------------------
# FoundryAgentNode: Foundry Agent を Agent Framework Executor としてラップ
# ---------------------------------------------------------------------------


class FoundryAgentNode(Executor):
    """Foundry Agent Service のエージェントを Agent Framework のワークフローノードとして使用する。

    - Responses API で Foundry Agent を呼び出し
    - FunctionTool の function_call を自動ハンドリング
    - 結果を WorkflowContext の shared state に保存して下流ノードで利用可能にする
    """

    def __init__(
        self,
        id: str,
        openai_client,
        agent_name: str,
        step_label: str = "",
        function_handler=None,
    ):
        super().__init__(id=id)
        self.openai_client = openai_client
        self.agent_name = agent_name
        self.step_label = step_label
        self.function_handler = function_handler

    @handler(input=str, output=str)
    async def invoke(self, input_text, ctx) -> None:  # type: ignore
        if self.step_label:
            print(f"\n{'=' * 60}")
            print(self.step_label)
            print("-" * 60)

        with tracer.start_as_current_span(
            f"foundry_agent.{self.agent_name}",
            attributes={
                "agent.name": self.agent_name,
                "agent.input_length": len(input_text),
            },
        ) as span:
            text, func_result = await asyncio.to_thread(self._call_agent, input_text, span)
            print(text)

            # shared state に保存
            ctx.set_state(f"{self.id}_response", text)
            if func_result is not None:
                ctx.set_state(f"{self.id}_function_result", func_result)

            await ctx.send_message(text)

    def _call_agent(self, input_text: str, span=None) -> tuple[str, dict | None]:
        """Foundry Agent を Responses API 経由で同期的に呼び出す。

        Returns:
            (応答テキスト, FunctionTool 実行結果 or None)
        """
        func_result = None

        conv = self.openai_client.conversations.create(
            items=[{"type": "message", "role": "user", "content": input_text}],
        )

        response = self.openai_client.responses.create(
            conversation=conv.id,
            extra_body={
                "agent_reference": {
                    "name": self.agent_name,
                    "type": "agent_reference",
                }
            },
        )

        # FunctionTool の function_call をハンドリング
        if self.function_handler:
            for output in response.output:
                if output.type == "function_call":
                    args = json.loads(output.arguments)
                    if span:
                        span.set_attribute("function_call.name", output.name if hasattr(output, "name") else "unknown")
                        span.set_attribute("function_call.args", json.dumps(args, ensure_ascii=False))
                    func_result = self.function_handler(args)
                    if span:
                        span.set_attribute("function_call.compliant", func_result.get("compliant", True))

                    self.openai_client.conversations.items.create(
                        conversation_id=conv.id,
                        items=[
                            {
                                "type": "function_call_output",
                                "call_id": output.call_id,
                                "output": json.dumps(func_result, ensure_ascii=False),
                            }
                        ],
                    )
                    response = self.openai_client.responses.create(
                        conversation=conv.id,
                        extra_body={
                            "agent_reference": {
                                "name": self.agent_name,
                                "type": "agent_reference",
                            }
                        },
                    )

        # エージェント応答テキスト
        text = response.output_text
        if span:
            span.set_attribute("agent.output_length", len(text))

        # Conversation クリーンアップ
        try:
            self.openai_client.conversations.delete(conversation_id=conv.id)
        except Exception:
            pass

        return text, func_result


# ---------------------------------------------------------------------------
# 条件分岐関数
# ---------------------------------------------------------------------------


def is_compliant(message: Any) -> bool:
    if isinstance(message, PolicyCheckResult):
        return message.compliant
    return True


def is_not_compliant(message: Any) -> bool:
    if isinstance(message, PolicyCheckResult):
        return not message.compliant
    return False


# ---------------------------------------------------------------------------
# カスタム Executor: データ変換 & ルーティング
# ---------------------------------------------------------------------------


import re


class RequestClarifierStep(Executor):
    """Step 0: RequestClarifier Foundry Agent を呼び出し、情報の過不足を判定する。

    出力は ClarificationResult で、下流の条件分岐エッジで complete/incomplete をルーティングする。
    """

    def __init__(self, openai_client):
        super().__init__(id="request_clarifier")
        self.openai_client = openai_client

    def _call_clarifier(self, user_input: str) -> dict:
        conv = self.openai_client.conversations.create(
            items=[{"type": "message", "role": "user", "content": user_input}],
        )
        response = self.openai_client.responses.create(
            conversation=conv.id,
            extra_body={
                "agent_reference": {
                    "name": REQUEST_CLARIFIER_AGENT,
                    "type": "agent_reference",
                }
            },
        )
        text = response.output_text
        try:
            self.openai_client.conversations.delete(conversation_id=conv.id)
        except Exception:
            pass

        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        raw = m.group(1) if m else text
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"complete": True, "enriched_request": user_input}

    @handler(input=str, output=ClarificationResult)
    async def run(self, user_input, ctx) -> None:  # type: ignore
        print("\n" + "=" * 60)
        print("🤖 Step 0: RequestClarifier — リクエスト情報確認 (Foundry Agent)")
        print("-" * 60)

        with tracer.start_as_current_span(
            "foundry_agent.RequestClarifier",
            attributes={"clarification.round": ctx.get_state("clarification_round", 1)},
        ):
            result = await asyncio.to_thread(self._call_clarifier, user_input)

        complete = result.get("complete", True)
        enriched = result.get("enriched_request", user_input)
        missing = result.get("missing_fields", [])
        question = result.get("question", "")

        if complete:
            print(f"  ✅ 必要情報が揃いました")
            print(f"  📝 整理済みリクエスト:")
            print(f"     {enriched if enriched else user_input}")
        else:
            print(f"  ℹ️  不足項目: {', '.join(missing)}")

        cr = ClarificationResult(
            complete=complete,
            enriched_request=enriched if enriched else user_input,
            missing_fields=missing,
            question=question,
            conversation=user_input,
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
    async def run(self, result, ctx) -> None:  # type: ignore
        ctx.set_state("clarified_request", result.enriched_request)
        await ctx.send_message(result.enriched_request)


class UserClarificationStep(Executor):
    """ClarificationResult (complete=false) → ユーザーに質問して回答を得る。

    回答を元のリクエストに追加し、str として出力する。
    再度 RequestClarifier に戻すためのノード。
    """

    def __init__(self):
        super().__init__(id="user_clarification")

    @handler(input=ClarificationResult, output=str)
    async def run(self, result, ctx) -> None:  # type: ignore
        round_num = ctx.get_state("clarification_round", 1)

        # ラウンド上限 → 現在の情報で続行
        if round_num >= 3:
            print("  ⏭️  確認回数上限に達しました。現在の情報で続行します。")
            ctx.set_state("clarified_request", result.conversation)
            await ctx.send_message(f"__CLARIFY_DONE__{result.conversation}")
            return

        # ユーザーに質問
        question = result.question or "追加情報を教えてください。"
        print()
        try:
            answer = input(f"  🤖 {question}\n  > ").strip()
        except EOFError:
            answer = ""

        if not answer:
            print("  ⏭️  スキップします")
            ctx.set_state("clarified_request", result.conversation)
            await ctx.send_message(f"__CLARIFY_DONE__{result.conversation}")
            return

        ctx.set_state("clarification_round", round_num + 1)
        updated = f"{result.conversation}\n{answer}"
        await ctx.send_message(updated)


def is_clarify_done(message: Any) -> bool:
    return isinstance(message, str) and message.startswith("__CLARIFY_DONE__")


def is_clarify_continue(message: Any) -> bool:
    return isinstance(message, str) and not message.startswith("__CLARIFY_DONE__")


@executor(id="extract_clarify_done")
async def extract_clarify_done(text: str, ctx: WorkflowContext[str]) -> None:
    """__CLARIFY_DONE__ プレフィックスを除去して enriched_request を取り出す"""
    await ctx.send_message(text.removeprefix("__CLARIFY_DONE__"))


class PlanConfirmationStep(Executor):
    """TravelPlanner の結果をユーザーに提示し、確認を得る。

    OK → PolicyChecker へ進む
    変更要望 → TravelPlanner に戻してプラン再生成
    """

    def __init__(self):
        super().__init__(id="plan_confirmation")

    @handler(input=str, output=str)
    async def run(self, plan_text, ctx) -> None:  # type: ignore
        ctx.set_state("raw_plan_text", plan_text)

        # JSON をパースしてユーザーフレンドリーに表示
        try:
            plan = json.loads(plan_text)
            print()
            print("=" * 60)
            print("📋 旅程プラン確認")
            print("-" * 60)
            legs = plan.get("transportation_legs", [])
            trip_type = plan.get("trip_type", "宿泊")
            print(f"  📌 種別:     {trip_type}")
            if legs:
                print("  🚄 交通手段:")
                for i, leg in enumerate(legs, 1):
                    method = leg.get("method", "—")
                    frm = leg.get("from", "—")
                    to = leg.get("to", "—")
                    cost = leg.get("cost", 0)
                    print(f"     {i}. {method}  {frm} → {to}  ¥{cost:,}")
            else:
                print(f"  🚄 交通手段: {plan.get('transportation', '—')}")
            print(f"  💴 交通費計: ¥{plan.get('transportation_cost', 0):,}")
            if trip_type == "宿泊":
                hotel_nights = plan.get("hotel_nights", 1)
                print(f"  🏨 宿泊先:   {plan.get('hotel', '—')}")
                print(f"  💴 宿泊費:   ¥{plan.get('hotel_cost_per_night', 0):,}/泊 × {hotel_nights}泊")
            else:
                print(f"  🏨 宿泊:     なし（日帰り）")
            print(f"  📅 スケジュール: {plan.get('schedule', '—')}")
            print(f"  💰 合計:     ¥{plan.get('total_cost', 0):,}")
            print("-" * 60)
        except json.JSONDecodeError:
            print()
            print("=" * 60)
            print("📋 旅程プラン確認")
            print("-" * 60)
            print(plan_text[:500])
            print("-" * 60)

        try:
            choice = input("  このプランでよろしいですか？\n  [Enter で確定 / 変更要望を入力]: ").strip()
        except EOFError:
            choice = ""

        if not choice or choice.lower() in ("y", "yes", "はい"):
            print("  ✅ プラン確定！規程チェックに進みます。")
            await ctx.send_message(plan_text)
        else:
            print(f"  🔄 変更要望を反映して再検索します...")
            original_request = ctx.get_state("clarified_request", "")
            revision_prompt = (
                f"以下の出張リクエストに対して旅程プランを再作成してください。\n\n"
                f"【元のリクエスト】\n{original_request}\n\n"
                f"【前回のプラン】\n{plan_text}\n\n"
                f"【変更要望】\n{choice}"
            )
            await ctx.send_message(f"__REVISE__{revision_prompt}")


def is_plan_confirmed(message: Any) -> bool:
    return isinstance(message, str) and not message.startswith("__REVISE__")


def is_plan_revision(message: Any) -> bool:
    return isinstance(message, str) and message.startswith("__REVISE__")


@executor(id="extract_revision")
async def extract_revision(text: str, ctx: WorkflowContext[str]) -> None:
    """__REVISE__ プレフィックスを除去して変更要望プロンプトを取り出す"""
    await ctx.send_message(text.removeprefix("__REVISE__"))


@executor(id="to_policy_input")
async def to_policy_input(plan_text: str, ctx: WorkflowContext[str]) -> None:
    """確認済みプラン → PolicyChecker の入力に変換"""
    prompt = f"以下の出張プランを旅費規程に照らしてチェックしてください:\n\n{plan_text}"
    await ctx.send_message(prompt)


class PolicyRouterStep(Executor):
    """PolicyChecker の結果から PolicyCheckResult を生成し、条件分岐に渡す。

    WorkflowContext の shared state に保存された FunctionTool の実行結果（compliant/details）を使用。
    """

    def __init__(self):
        super().__init__(id="policy_router")

    @handler(input=str, output=PolicyCheckResult)
    async def run(self, policy_text, ctx) -> None:  # type: ignore
        func_result = ctx.get_state("policy_checker_function_result")
        if func_result:
            compliant = func_result["compliant"]
            details = func_result["details"]
        else:
            compliant = "❌" not in policy_text
            details = [policy_text]

        result = PolicyCheckResult(
            compliant=compliant,
            details=details,
            policy_text=policy_text,
        )

        print(f"\n  📋 規程判定: {'✅ 適合' if compliant else '❌ 不適合'}")
        for d in details:
            print(f"  {d}")

        await ctx.send_message(result)


class ToApprovalInputStep(Executor):
    """PolicyCheckResult → ApprovalAgent 用プロンプトに変換"""

    def __init__(self):
        super().__init__(id="to_approval_input")

    @handler(input=PolicyCheckResult, output=str)
    async def run(self, result, ctx) -> None:  # type: ignore
        plan_text = ctx.get_state("travel_planner_response", "")
        prompt = (
            f"以下の情報を元に出張申請書を作成してください。\n\n"
            f"【旅程プラン】\n{plan_text}\n\n"
            f"【旅費規程チェック結果】\n{result.policy_text}"
        )
        await ctx.send_message(prompt)


@executor(id="handle_rejection")
async def handle_rejection(result: PolicyCheckResult, ctx: WorkflowContext[Never, str]) -> None:
    """規程不適合 — 差し戻し通知"""
    output = "\n❌ 旅費規程チェック: 不適合\n\n"
    output += "\n".join(f"  {d}" for d in result.details)
    output += "\n\nプランを修正して再申請してください。"
    await ctx.yield_output(output)


@executor(id="output_result")
async def output_result(text: str, ctx: WorkflowContext[Never, str]) -> None:
    """ワークフロー最終出力"""
    marker = "===== 出張申請書 ====="
    if marker in text:
        text = text[text.index(marker) :]
    await ctx.yield_output(text)


# ---------------------------------------------------------------------------
# ワークフロー構築
# ---------------------------------------------------------------------------


def create_workflow():
    """Foundry Agent + Agent Framework ハイブリッドワークフローを構築。

    事前に build_agents.py で作成済みの Foundry Agent を使用する。
    Returns:
        workflow
    """
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    openai_client = project_client.get_openai_client()

    print(f"📡 Foundry Agent を参照中...")
    print(f"  RequestClarifier: {REQUEST_CLARIFIER_AGENT}")
    print(f"  TravelPlanner:    {TRAVEL_PLANNER_AGENT}")
    print(f"  PolicyChecker:    {POLICY_CHECKER_AGENT}")
    print(f"  ApprovalAgent:    {APPROVAL_AGENT_NAME}")

    # =====================================================
    # Agent Framework ワークフローノードを構築
    # =====================================================

    # RequestClarifier ノード（Step 0: 情報確認）
    clarifier_node = RequestClarifierStep(openai_client=openai_client)
    clarification_to_request = ClarificationToRequestStep()
    user_clarification = UserClarificationStep()

    # Foundry Agent ノード（事前作成済みのエージェント名を参照）
    planner_node = FoundryAgentNode(
        id="travel_planner",
        openai_client=openai_client,
        agent_name=TRAVEL_PLANNER_AGENT,
        step_label="🔍 Step 1: Travel Planner Agent — 旅程検索 (Foundry Agent + Bing)",
    )

    checker_node = FoundryAgentNode(
        id="policy_checker",
        openai_client=openai_client,
        agent_name=POLICY_CHECKER_AGENT,
        step_label="📋 Step 2: Policy Checker Agent — 規程チェック (Foundry Agent + FunctionTool)",
        function_handler=evaluate_policy,
    )

    approval_node = FoundryAgentNode(
        id="approval_agent",
        openai_client=openai_client,
        agent_name=APPROVAL_AGENT_NAME,
        step_label="✅ Step 3: Approval Agent — 申請書作成 (Foundry Agent)",
    )

    # カスタム Executor ノード
    plan_confirm = PlanConfirmationStep()
    policy_router = PolicyRouterStep()
    to_approval = ToApprovalInputStep()

    # =====================================================
    # ワークフローグラフ構築
    # =====================================================
    #
    # RequestClarifier ──[complete]──→ ClarificationToRequest → TravelPlanner → PlanConfirm
    #       ↑            [incomplete]→ UserClarification ──[continue]──→ (loop back)
    #       └─────────────────────────────────────────────┘         ↓ [confirmed]     ↓ [revision]
    #                                  [done] → extract → TravelPlanner  PolicyInput   extract → TravelPlanner (loop)
    #
    workflow = (
        WorkflowBuilder(
            name="travel_request_workflow",
            description="AI 出張申請エージェント — 旅程検索・規程チェック・申請書作成ワークフロー",
            start_executor=clarifier_node,
        )
        # Step 0: 情報確認ループ
        .add_edge(clarifier_node, clarification_to_request, condition=is_clarification_complete)
        .add_edge(clarifier_node, user_clarification, condition=is_clarification_incomplete)
        .add_edge(user_clarification, clarifier_node, condition=is_clarify_continue)
        .add_edge(user_clarification, extract_clarify_done, condition=is_clarify_done)
        .add_edge(extract_clarify_done, planner_node)
        # Step 1-3: メインフロー
        .add_edge(clarification_to_request, planner_node)
        .add_edge(planner_node, plan_confirm)
        .add_edge(plan_confirm, to_policy_input, condition=is_plan_confirmed)
        .add_edge(plan_confirm, extract_revision, condition=is_plan_revision)
        .add_edge(extract_revision, planner_node)
        .add_edge(to_policy_input, checker_node)
        .add_edge(checker_node, policy_router)
        .add_edge(policy_router, to_approval, condition=is_compliant)
        .add_edge(policy_router, handle_rejection, condition=is_not_compliant)
        .add_edge(to_approval, approval_node)
        .add_edge(approval_node, output_result)
        .build()
    )

    return workflow


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


async def run_workflow():
    print("=" * 60)
    print("  🛫 AI 出張申請エージェント")
    print("  Agent Framework + Foundry Agent Service ハイブリッド版")
    print("=" * 60)
    print()

    default_request = (
        "来週の月曜に大阪出張したいです。"
    )

    user_input = input("出張リクエストを入力してください\n(Enter でデフォルト入力を使用):\n> ").strip()
    if not user_input:
        user_input = default_request
        print(f"\nデフォルト入力を使用:\n{user_input}")

    print()

    # --- ワークフロー実行 ---
    workflow = create_workflow()

    print()
    print("🚀 ワークフロー実行開始...")

    with tracer.start_as_current_span(
        "travel_request_workflow",
        attributes={"workflow.input": user_input},
    ) as workflow_span:
        events = await workflow.run(user_input)
        outputs = events.get_outputs()
        workflow_span.set_attribute("workflow.final_state", str(events.get_final_state()))
        workflow_span.set_attribute("workflow.output_count", len(outputs))

    # --- 結果表示 ---
    if outputs:
        print()
        print("=" * 60)
        print("📄 出張申請書:")
        print("-" * 60)
        print(outputs[-1])

    # --- 申請確認 (Human-in-the-Loop) ---
    print()
    print("=" * 60)
    confirm = input("📤 この内容で申請システムへ送信しますか？ [送信(y) / 取消(n)]: ").strip().lower()
    if confirm == "y":
        print("✅ 出張申請を申請システムへ送信しました！")
    else:
        print("🔄 申請を取り消しました。再度ワークフローを実行してください。")

    print()
    print(f"ワークフロー最終状態: {events.get_final_state()}")


if __name__ == "__main__":
    asyncio.run(run_workflow())
