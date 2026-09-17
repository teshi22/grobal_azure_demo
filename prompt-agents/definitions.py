"""Code-managed Foundry Prompt Agent definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azure.ai.projects.models import (
    MCPTool,
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonSchema,
    WebSearchApproximateLocation,
    WebSearchTool,
    WebSearchToolFilters,
)


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

ALLOWED_SEARCH_DOMAINS = (
    "jreast.co.jp",
    "jr-central.co.jp",
    "jr-odekake.net",
    "jrkyushu.co.jp",
    "jrhokkaido.co.jp",
    "jr-shikoku.co.jp",
    "smart-ex.jp",
    "eki-net.com",
    "tokyometro.jp",
    "kotsu.metro.tokyo.jp",
    "ekitan.com",
    "transit.yahoo.co.jp",
    "jorudan.co.jp",
    "navitime.co.jp",
    "travel.rakuten.co.jp",
    "jalan.net",
    "toyoko-inn.com",
    "route-inn.co.jp",
    "superhotel.co.jp",
)

EXTRACTED_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "departure": {"type": "string"},
        "destination": {"type": "string"},
        "schedule": {"type": "string"},
        "purpose": {"type": "string"},
    },
    "required": ["departure", "destination", "schedule", "purpose"],
    "additionalProperties": False,
}

PLAN_REVIEW_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
    },
    "required": ["approved"],
    "additionalProperties": False,
}

TRANSPORTATION_LEG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "direction": {"type": "string"},
        "method": {"type": "string"},
        "from": {"type": "string"},
        "to": {"type": "string"},
        "cost": {"type": "integer", "minimum": 0},
        "fare_type": {"type": "string"},
        "source_url": {"type": "string"},
        "source_title": {"type": "string"},
    },
    "required": [
        "direction",
        "method",
        "from",
        "to",
        "cost",
        "fare_type",
        "source_url",
        "source_title",
    ],
    "additionalProperties": False,
}

TRAVEL_PLAN_PROPERTIES: dict[str, Any] = {
    "departure": {"type": "string"},
    "destination": {"type": "string"},
    "purpose": {"type": "string"},
    "schedule": {"type": "string"},
    "trip_type": {"type": "string", "enum": ["日帰り", "宿泊"]},
    "transportation_legs": {
        "type": "array",
        "items": TRANSPORTATION_LEG_SCHEMA,
        "minItems": 1,
    },
    "transportation_cost": {"type": "integer", "minimum": 0},
    "hotel": {"type": ["string", "null"]},
    "hotel_cost_per_night": {"type": ["integer", "null"], "minimum": 0},
    "hotel_nights": {"type": ["integer", "null"], "minimum": 0},
    "total_cost": {"type": "integer", "minimum": 0},
    "distance_km": {"type": "number", "minimum": 0},
    "travel_time_hours": {"type": "number", "minimum": 0},
    "fare_basis": {"type": "string"},
    "searched_at": {"type": "string"},
}

TRAVEL_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": TRAVEL_PLAN_PROPERTIES,
    "required": list(TRAVEL_PLAN_PROPERTIES),
    "additionalProperties": False,
}

EVALUATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "needs_clarification",
                "draft_ready",
                "policy_blocked",
                "error",
            ],
        },
        "request": {
            "anyOf": [
                EXTRACTED_REQUEST_SCHEMA,
                {"type": "null"},
            ]
        },
        "clarification_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "itinerary": {
            "anyOf": [
                TRAVEL_PLAN_SCHEMA,
                {"type": "null"},
            ]
        },
        "fare_total": {
            "type": ["integer", "null"],
            "minimum": 0,
        },
        "policy": {
            "type": "object",
            "properties": {
                "compliant": {"type": ["boolean", "null"]},
                "details": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "narrative": {"type": "string"},
            },
            "required": ["compliant", "details", "narrative"],
            "additionalProperties": False,
        },
        "application_draft": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "fare_type": {"type": "string"},
                },
                "required": ["url", "title", "fare_type"],
                "additionalProperties": False,
            },
        },
        "diagnostics": {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string", "const": "1"},
                "scenario": {
                    "type": "string",
                    "const": "single_prompt_agent",
                },
                "case_id": {"type": "string"},
                "agent_versions": {
                    "type": "object",
                    "properties": {
                        "single_prompt_agent": {"type": "string"},
                    },
                    "required": ["single_prompt_agent"],
                    "additionalProperties": False,
                },
            },
            "required": [
                "schema_version",
                "scenario",
                "case_id",
                "agent_versions",
            ],
            "additionalProperties": False,
        },
    },
    "required": [
        "status",
        "request",
        "clarification_questions",
        "itinerary",
        "fare_total",
        "policy",
        "application_draft",
        "citations",
        "diagnostics",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PromptAgentSpec:
    name: str
    description: str
    prompt_file: str
    response_schema: dict[str, Any] | None = None
    response_schema_name: str = ""
    web_search: bool = False
    web_iq: bool = False
    mcp_submission: bool = False
    require_tool: bool = False

    @property
    def instructions(self) -> str:
        return (PROMPTS_DIR / self.prompt_file).read_text(encoding="utf-8").strip()

    def build_definition(
        self,
        model: str,
        *,
        mcp_connection_id: str = "",
        mcp_server_url: str = "",
        web_iq_connection_id: str = "",
        web_iq_server_url: str = "",
    ) -> PromptAgentDefinition:
        tools = []
        if self.web_search:
            tools.append(build_web_search_tool())
        if self.web_iq:
            if not web_iq_connection_id or not web_iq_server_url:
                raise ValueError(
                    "Web IQ connection ID and server URL are required "
                    "for the browsing agent"
                )
            tools.append(
                build_web_iq_mcp_tool(
                    connection_id=web_iq_connection_id,
                    server_url=web_iq_server_url,
                )
            )
        if self.mcp_submission:
            if not mcp_connection_id or not mcp_server_url:
                raise ValueError(
                    "MCP connection ID and server URL are required "
                    "for the submission agent"
                )
            tools.append(
                build_submission_mcp_tool(
                    connection_id=mcp_connection_id,
                    server_url=mcp_server_url,
                )
            )
        text = None
        if self.response_schema is not None:
            text = PromptAgentDefinitionTextOptions(
                format=TextResponseFormatJsonSchema(
                    name=self.response_schema_name,
                    schema=self.response_schema,
                    strict=True,
                )
            )
        return PromptAgentDefinition(
            model=model,
            instructions=self.instructions,
            tools=tools or None,
            tool_choice="required" if self.require_tool else None,
            text=text,
        )


def build_web_search_tool() -> WebSearchTool:
    return WebSearchTool(
        filters=WebSearchToolFilters(
            allowed_domains=list(ALLOWED_SEARCH_DOMAINS),
        ),
        user_location=WebSearchApproximateLocation(
            country="JP",
            region="Tokyo",
            city="Tokyo",
            timezone="Asia/Tokyo",
        ),
        search_context_size="high",
    )


def build_submission_mcp_tool(
    *,
    connection_id: str,
    server_url: str,
) -> MCPTool:
    return MCPTool(
        server_label="travel-request-submission",
        server_url=server_url,
        project_connection_id=connection_id,
        allowed_tools=[
            "prepare_travel_request_submission",
            "submit_travel_request_with_approval",
        ],
        require_approval="never",
    )


def build_web_iq_mcp_tool(
    *,
    connection_id: str,
    server_url: str,
) -> MCPTool:
    return MCPTool(
        server_label="travel-web-iq",
        server_url=server_url,
        project_connection_id=connection_id,
        allowed_tools=["web", "browse"],
        require_approval="never",
    )


PROMPT_AGENT_SPECS = (
    PromptAgentSpec(
        name="travel-request-clarifier",
        description="Extracts required travel request fields without guessing.",
        prompt_file="travel-request-clarifier.txt",
        response_schema=EXTRACTED_REQUEST_SCHEMA,
        response_schema_name="travel_request_fields",
    ),
    PromptAgentSpec(
        name="travel-request-planner",
        description="Searches trusted Japanese fare sources and builds a travel plan.",
        prompt_file="travel-request-planner.txt",
        response_schema=TRAVEL_PLAN_SCHEMA,
        response_schema_name="travel_plan",
        web_search=True,
        require_tool=True,
    ),
    PromptAgentSpec(
        name="travel-request-plan-reviewer",
        description=(
            "Classifies itinerary review replies as approval or revision requests."
        ),
        prompt_file="travel-request-plan-reviewer.txt",
        response_schema=PLAN_REVIEW_DECISION_SCHEMA,
        response_schema_name="travel_plan_review_decision",
    ),
    PromptAgentSpec(
        name="travel-request-policy-narrator",
        description="Narrates an authoritative deterministic travel policy result.",
        prompt_file="travel-request-policy-narrator.txt",
    ),
    PromptAgentSpec(
        name="travel-request-approval-writer",
        description="Writes a Japanese approval-ready travel request draft.",
        prompt_file="travel-request-approval-writer.txt",
    ),
    PromptAgentSpec(
        name="travel-request-single-agent",
        description=(
            "Runs the complete conversational travel request and MCP "
            "submission flow without application-defined modes."
        ),
        prompt_file="travel-request-single-agent.txt",
        web_iq=True,
        mcp_submission=True,
    ),
    PromptAgentSpec(
        name="travel-request-single-evaluator",
        description=(
            "Runs the single Prompt Agent scenario with a strict evaluation "
            "response schema and no interactive or submission tools."
        ),
        prompt_file="travel-request-single-evaluator.txt",
        response_schema=EVALUATION_OUTPUT_SCHEMA,
        response_schema_name="travel_evaluation_output",
        web_search=True,
    ),
)
