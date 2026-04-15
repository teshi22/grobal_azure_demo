"""TravelPlanner / PolicyChecker / Approval ノード

Foundry Agent を呼び出す各ステップノード。
"""

from typing import Any

from agent_framework import Executor, Message, WorkflowContext, handler

from app.config import settings
from app.workflow.models import PolicyCheckResult
from app.workflow.nodes.foundry_base import FoundryAgentNode
from app.workflow.policy import evaluate_policy


class MessageToStrStep(Executor):
    """list[Message] → str 変換 (入口ノード)"""

    def __init__(self):
        super().__init__(id="message_to_str")

    @handler(input=list[Message], output=str)
    async def run(self, messages, ctx) -> None:
        text = " ".join(m.text or "" for m in messages if m.text).strip()
        await ctx.send_message(text)


class ToPolicyInputStep(Executor):
    """確認済みプラン → PolicyChecker 入力に変換"""

    def __init__(self):
        super().__init__(id="to_policy_input")

    @handler(input=str, output=str)
    async def run(self, plan_text, ctx) -> None:
        prompt = f"以下の出張プランを旅費規程に照らしてチェックしてください:\n\n{plan_text}"
        await ctx.send_message(prompt)


class PolicyRouterStep(Executor):
    """PolicyChecker の結果から PolicyCheckResult を生成し条件分岐に渡す"""

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
        result = PolicyCheckResult(
            compliant=compliant, details=details, policy_text=policy_text
        )
        await ctx.send_message(result)


class ToApprovalInputStep(Executor):
    """PolicyCheckResult → ApprovalAgent 用プロンプトに変換"""

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
    """最終出力: 申請書テキストを出力"""

    def __init__(self):
        super().__init__(id="output_result")

    @handler(input=str)
    async def run(self, text, ctx) -> None:
        marker = "===== 出張申請書 ====="
        if marker in text:
            text = text[text.index(marker) :]
        await ctx.yield_output(text)


# ---------------------------------------------------------------------------
# 条件分岐
# ---------------------------------------------------------------------------
def is_compliant(message: Any) -> bool:
    return isinstance(message, PolicyCheckResult) and message.compliant


def is_not_compliant(message: Any) -> bool:
    return isinstance(message, PolicyCheckResult) and not message.compliant
