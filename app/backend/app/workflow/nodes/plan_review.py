"""PlanReview ノード — HITL でプランをユーザーに確認"""

from typing import Any

from agent_framework import Executor, WorkflowContext, handler, response_handler

from app.workflow.models import PlanReviewRequest, PlanReviewResponse


class PlanReviewStep(Executor):
    """TravelPlanner の結果をユーザーに提示し、HITL で確認を得る"""

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
