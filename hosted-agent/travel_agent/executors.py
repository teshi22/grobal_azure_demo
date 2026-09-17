"""Agent Framework executors for the travel request workflow."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from agent_framework import Executor, Message, handler, response_handler

from .agents import TravelAgents
from .date_resolver import resolve_schedule
from .evaluation import EvaluationMode
from .models import (
    ApprovalDocument,
    ClarificationRequest,
    ClarificationResponse,
    ClarificationResult,
    ExtractedRequest,
    PlanReviewRequest,
    PlanReviewResponse,
    PolicyOutcome,
    RequestConfirmationRequest,
    RequestConfirmationResponse,
    SubmissionApprovalRequest,
    TravelPlan,
    fare_evidence_errors,
)
from .policy import evaluate_policy
from .submission_agent import PendingMCPApproval

_REQUIRED_FIELDS = ("departure", "destination", "schedule", "purpose")
_FIELD_LABELS = {
    "departure": "出発地",
    "destination": "目的地",
    "schedule": "日程",
    "purpose": "出張目的",
}
_DEFAULT_DEPARTURE = "大阪"


def _extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    if not fenced:
        obj = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = obj.group(0) if obj else text
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("Agent response must be a JSON object")
    return value


def _response_text(response: Any, agent_name: str) -> str:
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{agent_name} returned an empty text response")
    return text


class MessageToTextStep(Executor):
    def __init__(self, evaluation: EvaluationMode) -> None:
        super().__init__(id="message_to_text")
        self._evaluation = evaluation

    @handler(input=list[Message], output=str, workflow_output=str)
    async def run(self, messages, ctx) -> None:
        text = " ".join(message.text or "" for message in messages).strip()
        evaluation = self._evaluation.begin(text, ctx)
        if evaluation.output_json:
            await ctx.yield_output(evaluation.output_json)
            return
        if self._evaluation.is_active(ctx):
            await ctx.send_message(evaluation.input_text)
            return
        if self._evaluation.is_playground(ctx):
            await ctx.send_message(evaluation.input_text)
            return

        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            envelope = None
        if isinstance(envelope, dict) and isinstance(envelope.get("message"), str):
            ctx.set_state("conversation_id", str(envelope.get("conversation_id", "")))
            text = envelope["message"].strip()
        await ctx.send_message(text)


class RequestClarifierStep(Executor):
    def __init__(self, agents: TravelAgents) -> None:
        super().__init__(id="request_clarifier")
        self._agent = agents.clarifier

    @handler(input=str, output=ClarificationResult)
    async def run(self, user_input, ctx) -> None:
        response = await self._agent.run(user_input)
        extracted = ExtractedRequest.model_validate(
            _extract_json(_response_text(response, "request clarifier"))
        )
        previous = ctx.get_state("request_fields") or {}
        fields = {
            key: getattr(extracted, key) or previous.get(key, "")
            for key in _REQUIRED_FIELDS
        }
        if not fields["departure"]:
            fields["departure"] = _DEFAULT_DEPARTURE

        schedule_question = resolve_schedule(fields)
        missing = [
            _FIELD_LABELS[key] for key in _REQUIRED_FIELDS if not fields.get(key)
        ]
        question = schedule_question or (
            f"{'、'.join(missing)}を教えてください。" if missing else ""
        )
        ctx.set_state("request_fields", fields)
        ctx.set_state("original_input", user_input)
        await ctx.send_message(
            ClarificationResult(
                complete=not question,
                enriched_request="\n".join(
                    [
                        f"出発地: {fields['departure']}",
                        f"目的地: {fields['destination']}",
                        f"日程: {fields['schedule']}",
                        f"目的: {fields['purpose']}",
                    ]
                )
                if not question
                else "",
                missing_fields=["日程"] if schedule_question else missing,
                question=question,
            )
        )


class ClarificationStep(Executor):
    def __init__(self, evaluation: EvaluationMode) -> None:
        super().__init__(id="clarification")
        self._evaluation = evaluation

    @handler(input=ClarificationResult, output=str, workflow_output=str)
    async def request(self, result, ctx) -> None:
        if self._evaluation.is_active(ctx):
            await ctx.yield_output(
                self._evaluation.needs_clarification(ctx, result)
            )
            return
        await ctx.request_info(
            request_data=ClarificationRequest(
                question=result.question,
                missing_fields=result.missing_fields,
                original_input=ctx.get_state("original_input", ""),
                message=result.question,
                data={
                    "question": result.question,
                    "missing_fields": result.missing_fields,
                },
            ),
            response_type=str,
        )

    @response_handler(
        request=ClarificationRequest,
        response=str,
        output=str,
    )
    async def respond(
        self,
        original: ClarificationRequest,
        response: str,
        ctx,
    ) -> None:
        parsed = ClarificationResponse.convert_from_payload(response)
        await ctx.send_message(
            f"{original.original_input}\n{parsed.answer}",
            target_id="request_clarifier",
        )


class RequestConfirmationStep(Executor):
    def __init__(self, evaluation: EvaluationMode) -> None:
        super().__init__(id="request_confirmation")
        self._evaluation = evaluation

    @handler(input=ClarificationResult, output=str)
    async def request(self, result, ctx) -> None:
        if self._evaluation.is_active(ctx):
            await ctx.send_message(
                result.enriched_request,
                target_id="travel_planner",
            )
            return
        await ctx.request_info(
            request_data=RequestConfirmationRequest(
                enriched_request=result.enriched_request,
                fields=ctx.get_state("request_fields") or {},
                message="以下の内容で旅程を検索します。よろしいですか？",
                data=ctx.get_state("request_fields") or {},
            ),
            response_type=str,
        )

    @response_handler(
        request=RequestConfirmationRequest,
        response=str,
        output=str,
    )
    async def respond(
        self,
        original: RequestConfirmationRequest,
        response: str,
        ctx,
    ) -> None:
        parsed = RequestConfirmationResponse.convert_from_payload(response)
        if parsed.confirmed:
            await ctx.send_message(
                original.enriched_request,
                target_id="travel_planner",
            )
            return
        await ctx.send_message(
            parsed.revision,
            target_id="request_clarifier",
        )


class TravelPlannerStep(Executor):
    def __init__(self, agents: TravelAgents) -> None:
        super().__init__(id="travel_planner")
        self._agent = agents.planner

    @handler(input=str, output=str)
    async def run(self, prompt, ctx) -> None:
        plan = await self._search_plan(prompt)
        errors = fare_evidence_errors(plan)
        if errors:
            plan = await self._search_plan(
                (
                    "前回の検索結果には運賃の根拠不足があります。必ずWeb Searchを再実行し、"
                    "各区間の正確な運賃種別、金額、根拠URLを補正してください。\n"
                    f"元の依頼: {prompt}\n"
                    f"検証エラー: {json.dumps(errors, ensure_ascii=False)}\n"
                    f"前回結果: {plan.model_dump_json()}"
                )
            )
            errors = fare_evidence_errors(plan)
            if errors:
                raise ValueError(
                    "Web search did not return verifiable fares: "
                    + "; ".join(errors)
                )

        plan.searched_at = datetime.now(timezone.utc).isoformat()
        plan_json = plan.model_dump_json()
        ctx.set_state("current_plan", plan_json)
        await ctx.send_message(plan_json)

    async def _search_plan(self, prompt: str) -> TravelPlan:
        response = await self._agent.run(prompt)
        value = getattr(response, "value", None)
        if isinstance(value, TravelPlan):
            return value
        if value is not None:
            return TravelPlan.model_validate(value)
        return TravelPlan.model_validate(
            _extract_json(_response_text(response, "travel planner"))
        )


class PlanReviewStep(Executor):
    def __init__(
        self,
        agents: TravelAgents,
        evaluation: EvaluationMode,
    ) -> None:
        super().__init__(id="plan_review")
        self._agent = agents.plan_reviewer
        self._evaluation = evaluation

    @handler(input=str, output=str)
    async def request(self, plan_json, ctx) -> None:
        if self._evaluation.is_active(ctx):
            await ctx.send_message(plan_json, target_id="policy_check")
            return
        await ctx.request_info(
            request_data=PlanReviewRequest(
                plan_json=plan_json,
                message="この旅程プランでよろしいですか？",
                data=json.loads(plan_json),
            ),
            response_type=str,
        )

    @response_handler(
        request=PlanReviewRequest,
        response=str,
        output=str,
    )
    async def respond(
        self,
        original: PlanReviewRequest,
        response: str,
        ctx,
    ) -> None:
        decision_response = await self._agent.run(
            json.dumps(
                {
                    "plan": json.loads(original.plan_json),
                    "user_reply": response,
                },
                ensure_ascii=False,
            )
        )
        parsed = PlanReviewResponse.convert_from_payload(
            _response_text(decision_response, "plan reviewer")
        )
        if parsed.approved:
            await ctx.send_message(original.plan_json, target_id="policy_check")
            return
        await ctx.send_message(
            (
                "以下の旅程プランを変更してください。\n"
                f"前回のプラン: {original.plan_json}\n"
                f"変更要望: {response}"
            ),
            target_id="travel_planner",
        )


class PolicyCheckStep(Executor):
    def __init__(self, agents: TravelAgents) -> None:
        super().__init__(id="policy_check")
        self._agent = agents.policy

    @handler(input=str, output=PolicyOutcome)
    async def run(self, plan_json, ctx) -> None:
        plan = TravelPlan.model_validate_json(plan_json)
        result = evaluate_policy(plan)
        narrative_response = await self._agent.run(
            json.dumps(
                {
                    "compliant": result["compliant"],
                    "details": result["details"],
                    "plan": plan.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        )
        await ctx.send_message(
            PolicyOutcome(
                compliant=bool(result["compliant"]),
                details=list(result["details"]),
                narrative=_response_text(
                    narrative_response,
                    "policy narrator",
                ),
                plan_json=plan_json,
            )
        )


class PolicyReplanStep(Executor):
    def __init__(self) -> None:
        super().__init__(id="policy_replan")

    @handler(input=PolicyOutcome, output=str)
    async def run(self, outcome, ctx) -> None:
        await ctx.send_message(
            (
                "以下の旅程は旅費規程に不適合です。違反を解消した新しいプランを"
                f"検索してください。\nプラン: {outcome.plan_json}\n"
                f"違反: {json.dumps(outcome.details, ensure_ascii=False)}"
            )
        )


class ApprovalDocumentStep(Executor):
    def __init__(
        self,
        agents: TravelAgents,
        evaluation: EvaluationMode,
    ) -> None:
        super().__init__(id="approval_document")
        self._agent = agents.approval
        self._submission = agents.submission
        self._evaluation = evaluation

    @handler(
        input=PolicyOutcome,
        output=ApprovalDocument,
        workflow_output=str,
    )
    async def run(self, outcome, ctx) -> None:
        response = await self._agent.run(
            f"旅程JSON:\n{outcome.plan_json}\n\n規程チェック:\n{outcome.narrative}"
        )
        plan_hash = hashlib.sha256(outcome.plan_json.encode("utf-8")).hexdigest()
        document = ApprovalDocument(
            application_text=_response_text(
                response,
                "approval writer",
            ),
            plan_json=outcome.plan_json,
            policy_narrative=outcome.narrative,
            plan_hash=plan_hash,
        )
        if self._evaluation.is_active(ctx):
            await ctx.yield_output(
                self._evaluation.draft_ready(ctx, document, outcome)
            )
            return
        if self._evaluation.is_playground(ctx):
            await ctx.yield_output(
                self._evaluation.playground_draft(ctx, document, outcome)
            )
            return
        prepare_arguments = {
            "application_text": document.application_text,
            "agent_scenario": "agent_framework_workflow",
            "application_data": json.loads(document.plan_json),
            "policy_result": document.policy_narrative,
        }
        conversation_id = str(
            ctx.get_state("conversation_id", "")
        ).strip()
        if conversation_id:
            prepare_arguments["conversation_id"] = conversation_id
        prepared = await self._submission.prepare(
            prepare_arguments
        )
        document.approval_id = str(prepared["approval_id"])
        await ctx.send_message(document)


class SubmissionApprovalStep(Executor):
    def __init__(self, agents: TravelAgents) -> None:
        super().__init__(id="submission_approval")
        self._submission = agents.submission

    @handler(input=ApprovalDocument, workflow_output=str)
    async def request(self, document, ctx) -> None:
        ctx.set_state("approval_document", document.model_dump())
        pending = await self._submission.request_submission(
            document.approval_id
        )
        await ctx.request_info(
            request_data=SubmissionApprovalRequest(
                approval_id=document.approval_id,
                application_text=document.application_text,
                plan_json=document.plan_json,
                policy_narrative=document.policy_narrative,
                approval_request_id=pending.request_id,
                tool_name=pending.tool_name,
                tool_arguments=pending.arguments,
                server_label=pending.server_label,
                agent_session=pending.session,
                message="旅費規程に適合しました。申請を送信しますか？",
                data={
                    "application_text": document.application_text,
                    "plan": json.loads(document.plan_json),
                    "policy_result": document.policy_narrative,
                },
            ),
            response_type=bool,
        )

    @response_handler(
        request=SubmissionApprovalRequest,
        response=bool,
        workflow_output=str,
    )
    async def respond(
        self,
        original: SubmissionApprovalRequest,
        response: bool,
        ctx,
    ) -> None:
        result = await self._submission.resolve_submission(
            PendingMCPApproval(
                request_id=original.approval_request_id,
                tool_name=original.tool_name,
                arguments=original.tool_arguments,
                server_label=original.server_label,
                session=original.agent_session,
            ),
            response,
        )
        if not result.get("submitted", False):
            await ctx.yield_output(str(result["message"]))
            return
        request_id = str(result.get("request_id", ""))
        document = ctx.get_state("approval_document") or {}
        await ctx.yield_output(
            (
                f"{document.get('application_text', '')}\n\n"
                "✅ 出張申請書を送信しました。"
                f"（申請番号: {request_id}）"
            ).strip()
        )


def is_complete(value: Any) -> bool:
    return isinstance(value, ClarificationResult) and value.complete


def is_incomplete(value: Any) -> bool:
    return isinstance(value, ClarificationResult) and not value.complete


def is_compliant(value: Any) -> bool:
    return isinstance(value, PolicyOutcome) and value.compliant


def is_noncompliant(value: Any) -> bool:
    return isinstance(value, PolicyOutcome) and not value.compliant
