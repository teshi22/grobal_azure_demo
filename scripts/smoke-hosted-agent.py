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


def has_function_call(response_payload: dict) -> bool:
    return any(
        item.get("type") == "function_call"
        for item in response_payload.get("output", [])
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
        first_text = response_text(payload)
        if not first_text:
            print(
                "Hosted Agent did not return a chat response.",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1
        if has_function_call(payload):
            print(
                "Hosted Agent exposed a function call to the chat client.",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1

        if "教えてください" in first_text:
            chat_reply = (
                "出発地は東京、目的地は大阪、日程は2026年10月15日の"
                "日帰り、目的は顧客会議です。"
            )
            expected_next_text = "以下の内容で旅程を検索します"
        elif "以下の内容で旅程を検索します" in first_text:
            chat_reply = "OK"
            expected_next_text = "この旅程プランでよろしいですか"
        else:
            print(
                "Hosted Agent returned an unexpected first chat response.",
                file=sys.stderr,
            )
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1

        resumed_response = openai_client.responses.create(
            previous_response_id=response.id,
            input=chat_reply,
            extra_body={"agent_session_id": conversation_id},
        )
    finally:
        project_client.close()

    resumed_payload = resumed_response.model_dump(mode="json")
    resumed_text = response_text(resumed_payload)
    if expected_next_text not in resumed_text:
        print(
            "Hosted Agent did not resume to the expected chat response.",
            file=sys.stderr,
        )
        print(json.dumps(resumed_payload, ensure_ascii=False), file=sys.stderr)
        return 1
    if has_function_call(resumed_payload):
        print(
            "Hosted Agent exposed a function call after chat resume.",
            file=sys.stderr,
        )
        print(json.dumps(resumed_payload, ensure_ascii=False), file=sys.stderr)
        return 1

    print(
        "Hosted Agent displayed and resumed HITL using chat text only."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
