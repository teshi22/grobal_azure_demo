"""Central policy and state helpers for side-effect-free evaluation runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError

from .models import (
    ApprovalDocument,
    ClarificationResult,
    EvaluationCitation,
    EvaluationDiagnostics,
    EvaluationInputEnvelope,
    EvaluationOutput,
    EvaluationPolicyResult,
    ExtractedRequest,
    PolicyOutcome,
    TravelPlan,
)


@dataclass(frozen=True)
class EvaluationStart:
    input_text: str
    output_json: str = ""


class EvaluationMode:
    _STATE_KEY = "evaluation_envelope"

    def __init__(self, agent_versions: Mapping[str, str]) -> None:
        self._agent_versions = dict(agent_versions)

    def begin(self, text: str, ctx: Any) -> EvaluationStart:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            self._leave(ctx)
            return EvaluationStart(input_text=text)
        if not isinstance(payload, dict) or payload.get("mode") != "evaluation":
            self._leave(ctx)
            return EvaluationStart(input_text=text)

        try:
            envelope = EvaluationInputEnvelope.model_validate(payload)
        except ValidationError as exc:
            self._leave(ctx)
            case_id = str(payload.get("case_id", ""))
            output = self._output(
                case_id=case_id,
                status="error",
                policy=EvaluationPolicyResult(
                    narrative=f"Invalid evaluation envelope: {exc}"
                ),
            )
            return EvaluationStart(
                input_text="",
                output_json=output.model_dump_json(),
            )

        for key in (
            "request_fields",
            "original_input",
            "current_plan",
            "approval_document",
        ):
            ctx.set_state(key, None)
        ctx.set_state(self._STATE_KEY, envelope.model_dump())
        return EvaluationStart(input_text=envelope.input)

    def is_active(self, ctx: Any) -> bool:
        return bool(ctx.get_state(self._STATE_KEY))

    def _leave(self, ctx: Any) -> None:
        if self.is_active(ctx):
            ctx.set_state(self._STATE_KEY, None)

    def needs_clarification(
        self,
        ctx: Any,
        result: ClarificationResult,
    ) -> str:
        questions = [result.question] if result.question else []
        return self._output(
            case_id=self._case_id(ctx),
            status="needs_clarification",
            request=self._request(ctx),
            clarification_questions=questions,
        ).model_dump_json()

    def policy_blocked(
        self,
        ctx: Any,
        plan: TravelPlan,
        outcome: PolicyOutcome,
    ) -> str:
        return self._output(
            case_id=self._case_id(ctx),
            status="policy_blocked",
            request=self._request(ctx),
            itinerary=plan,
            policy=EvaluationPolicyResult(
                compliant=outcome.compliant,
                details=outcome.details,
                narrative=outcome.narrative,
            ),
            citations=self._citations(plan),
        ).model_dump_json()

    def draft_ready(
        self,
        ctx: Any,
        document: ApprovalDocument,
        outcome: PolicyOutcome,
    ) -> str:
        plan = TravelPlan.model_validate_json(document.plan_json)
        return self._output(
            case_id=self._case_id(ctx),
            status="draft_ready",
            request=self._request(ctx),
            itinerary=plan,
            policy=EvaluationPolicyResult(
                compliant=outcome.compliant,
                details=outcome.details,
                narrative=outcome.narrative,
            ),
            application_draft=document.application_text,
            citations=self._citations(plan),
        ).model_dump_json()

    def _case_id(self, ctx: Any) -> str:
        envelope = ctx.get_state(self._STATE_KEY) or {}
        return str(envelope.get("case_id", ""))

    @staticmethod
    def _request(ctx: Any) -> ExtractedRequest:
        return ExtractedRequest.model_validate(
            ctx.get_state("request_fields") or {}
        )

    def _output(
        self,
        *,
        case_id: str,
        status: str,
        request: ExtractedRequest | None = None,
        clarification_questions: list[str] | None = None,
        itinerary: TravelPlan | None = None,
        policy: EvaluationPolicyResult | None = None,
        application_draft: str = "",
        citations: list[EvaluationCitation] | None = None,
    ) -> EvaluationOutput:
        return EvaluationOutput(
            status=status,
            request=request,
            clarification_questions=clarification_questions or [],
            itinerary=itinerary,
            policy=policy or EvaluationPolicyResult(),
            application_draft=application_draft,
            citations=citations or [],
            diagnostics=EvaluationDiagnostics(
                scenario="agent_framework_workflow",
                case_id=case_id,
                agent_versions=self._agent_versions,
            ),
        )

    @staticmethod
    def _citations(plan: TravelPlan) -> list[EvaluationCitation]:
        citations: list[EvaluationCitation] = []
        seen: set[tuple[str, str, str]] = set()
        for leg in plan.transportation_legs:
            citation = (
                str(leg.get("source_url", "")).strip(),
                str(leg.get("source_title", "")).strip(),
                str(leg.get("fare_type", "")).strip(),
            )
            if not citation[0] or citation in seen:
                continue
            seen.add(citation)
            citations.append(
                EvaluationCitation(
                    url=citation[0],
                    title=citation[1],
                    fare_type=citation[2],
                )
            )
        return citations
