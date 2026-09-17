"""Build the Hosted Agent workflow."""

from agent_framework import WorkflowBuilder

from .agents import TravelAgents
from .evaluation import EvaluationMode
from .executors import (
    ApprovalDocumentStep,
    ClarificationStep,
    MessageToTextStep,
    PlanReviewStep,
    PolicyCheckStep,
    PolicyReplanStep,
    RequestClarifierStep,
    RequestConfirmationStep,
    SubmissionConfirmationStep,
    SubmitTravelRequestStep,
    TravelPlannerStep,
    is_complete,
    is_compliant,
    is_incomplete,
    is_noncompliant,
)


def build_workflow(agents: TravelAgents):
    evaluation = EvaluationMode(agents.versions)
    start = MessageToTextStep(evaluation)
    clarifier = RequestClarifierStep(agents)
    clarification = ClarificationStep(evaluation)
    request_confirmation = RequestConfirmationStep(evaluation)
    planner = TravelPlannerStep(agents)
    plan_review = PlanReviewStep(agents, evaluation)
    policy = PolicyCheckStep(agents)
    policy_replan = PolicyReplanStep()
    approval_document = ApprovalDocumentStep(agents, evaluation)
    submission_confirmation = SubmissionConfirmationStep()
    submit = SubmitTravelRequestStep()

    return (
        WorkflowBuilder(
            name="travel-request-workflow",
            description="Authenticated travel planning and submission workflow.",
            start_executor=start,
            output_from=[
                start,
                clarification,
                approval_document,
                submission_confirmation,
                submit,
            ],
        )
        .add_edge(start, clarifier)
        .add_edge(clarifier, clarification, condition=is_incomplete)
        .add_edge(clarifier, request_confirmation, condition=is_complete)
        .add_edge(clarification, clarifier)
        .add_edge(request_confirmation, planner)
        .add_edge(request_confirmation, clarifier)
        .add_edge(planner, plan_review)
        .add_edge(plan_review, planner)
        .add_edge(plan_review, policy)
        .add_edge(policy, policy_replan, condition=is_noncompliant)
        .add_edge(policy_replan, planner)
        .add_edge(policy, approval_document, condition=is_compliant)
        .add_edge(approval_document, submission_confirmation)
        .add_edge(submission_confirmation, submit)
        .build()
    )
