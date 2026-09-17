"""Smoke-test the deployed travel request Hosted Agent."""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", required=True)
    return parser.parse_args()


def request_info_call(response_payload: dict) -> dict | None:
    for item in response_payload.get("output", []):
        if (
            item.get("type") == "function_call"
            and item.get("name") == "request_info"
        ):
            return item
    return None


def request_type(call: dict) -> str:
    arguments = json.loads(call["arguments"])
    request_event = arguments.get("request_event", {})
    data = request_event.get("data", {})
    if isinstance(data, str):
        data = json.loads(data)
    return str(data.get("type", ""))


def response_text(response_payload: dict) -> str:
    direct = response_payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    return "\n".join(
        str(content.get("text", "")).strip()
        for item in response_payload.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") == "output_text"
        and str(content.get("text", "")).strip()
    )


def main() -> int:
    args = parse_args()
    conversation_id = f"smoke-{uuid.uuid4()}"
    project_client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=DefaultAzureCredential(),
    )
    try:
        openai_client = project_client.get_openai_client(
            agent_name=args.agent_name
        )
        response = openai_client.responses.create(
            input=json.dumps(
                {
                    "conversation_id": conversation_id,
                    "message": (
                        "2026年10月15日に東京から大阪へ日帰りで"
                        "顧客会議に行きたいです。"
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            extra_body={"agent_session_id": conversation_id},
        )
        payload = response.model_dump(mode="json")
        call = request_info_call(payload)
        if not call:
            print(
                "Hosted Agent did not return the expected request_info call.",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1
        if not response_text(payload):
            print(
                "Hosted Agent did not expose the HITL request as chat text.",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1

        first_request_type = request_type(call)
        if first_request_type == "clarification":
            chat_reply = (
                "出発地は東京、目的地は大阪、日程は2026年10月15日の"
                "日帰り、目的は顧客会議です。"
            )
            expected_next_type = "request_confirmation"
        elif first_request_type == "request_confirmation":
            chat_reply = "OK"
            expected_next_type = "plan_review"
        else:
            print(
                "Hosted Agent returned an unexpected first HITL request: "
                f"{first_request_type}",
                file=sys.stderr,
            )
            return 1

        resumed_response = openai_client.responses.create(
            previous_response_id=response.id,
            input=chat_reply,
            extra_body={"agent_session_id": conversation_id},
        )
    finally:
        project_client.close()

    resumed_payload = resumed_response.model_dump(mode="json")
    resumed_call = request_info_call(resumed_payload)
    if not resumed_call or request_type(resumed_call) != expected_next_type:
        print(
            "Hosted Agent did not resume to the expected "
            f"{expected_next_type} call.",
            file=sys.stderr,
        )
        print(json.dumps(resumed_payload, ensure_ascii=False), file=sys.stderr)
        return 1

    print(
        "Hosted Agent displayed and resumed direct chat HITL from "
        f"{first_request_type} to {expected_next_type}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
