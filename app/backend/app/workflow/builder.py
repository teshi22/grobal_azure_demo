"""ワークフロービルダー — Agent Framework グラフ構築"""

from agent_framework import WorkflowBuilder

from app.config import settings
from app.services.mcp_client import call_submit_tool_sync
from app.workflow.nodes.clarifier import (
    ClarificationDirectStep,
    ClarificationToRequestStep,
    RequestClarifierStep,
    UserClarificationStep,
    is_clarification_complete,
    is_clarification_incomplete,
)
from app.workflow.nodes.foundry_base import FoundryAgentNode
from app.workflow.nodes.plan_review import PlanReviewStep
from app.workflow.nodes.travel_planner import (
    HandleRejectionStep,
    MessageToStrStep,
    PolicyRouterStep,
    ToApprovalInputStep,
    ToPolicyInputStep,
    is_compliant,
    is_not_compliant,
)
from app.workflow.policy import evaluate_policy


def create_workflow_builder() -> WorkflowBuilder:
    """HITL 対応ワークフロービルダーを構築する。

    グラフ:
      MessageToStr → RequestClarifier ─[complete]─→ ClarificationToRequest → TravelPlanner
                          ↑              [incomplete]
                          |                   ↓
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
                                          ApprovalAgent (MCP で申請送信)
    """
    # ノードインスタンス
    message_to_str = MessageToStrStep()
    request_clarifier = RequestClarifierStep()
    clarification_to_request = ClarificationToRequestStep()
    user_clarification = UserClarificationStep()
    clarification_direct = ClarificationDirectStep()

    travel_planner = FoundryAgentNode(
        id="travel_planner", agent_name=settings.travel_planner_agent
    )
    plan_review = PlanReviewStep()
    to_policy_input = ToPolicyInputStep()

    policy_checker = FoundryAgentNode(
        id="policy_checker",
        agent_name=settings.policy_checker_agent,
        function_handler=evaluate_policy,
    )
    policy_router = PolicyRouterStep()
    to_approval_input = ToApprovalInputStep()

    approval_agent = FoundryAgentNode(
        id="approval_agent",
        agent_name=settings.approval_agent,
        function_handler=call_submit_tool_sync,
        is_terminal=True,
    )

    handle_rejection = HandleRejectionStep()

    # グラフ構築
    builder = (
        WorkflowBuilder(start_executor=message_to_str)
        # Step 0: 情報確認ループ
        .add_edge(message_to_str, request_clarifier)
        .add_edge(
            request_clarifier,
            clarification_to_request,
            condition=is_clarification_complete,
        )
        .add_edge(
            request_clarifier,
            user_clarification,
            condition=is_clarification_incomplete,
        )
        .add_edge(user_clarification, request_clarifier)  # 追加情報 → 再判定
        .add_edge(user_clarification, clarification_direct)  # ラウンド上限
        .add_edge(clarification_direct, travel_planner)
        .add_edge(clarification_to_request, travel_planner)
        # Step 1: TravelPlanner → PlanReview (HITL)
        .add_edge(travel_planner, plan_review)
        .add_edge(plan_review, travel_planner)  # 変更要望 → 再検索
        .add_edge(plan_review, to_policy_input)  # OK → 規程チェック
        # Step 2: PolicyChecker
        .add_edge(to_policy_input, policy_checker)
        .add_edge(policy_checker, policy_router)
        .add_edge(policy_router, to_approval_input, condition=is_compliant)
        .add_edge(policy_router, handle_rejection, condition=is_not_compliant)
        # Step 3: ApprovalAgent (MCP 連携で申請送信)
        .add_edge(to_approval_input, approval_agent)
    )

    return builder
