"""Smoke-test the deployed travel request Hosted Agent."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", required=True)
    parser.add_argument(
        "--decision",
        choices=("pending", "approve", "reject"),
        default="pending",
        help=(
            "Stop at native approval, or resume it with an approval decision."
        ),
    )
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


def mcp_approval_requests(response_payload: dict) -> list[dict]:
    return [
        item
        for item in response_payload.get("output", [])
        if item.get("type") == "mcp_approval_request"
    ]


def next_reply(text: str) -> str | None:
    if "教えてください" in text:
        return (
            "出発地は東京、目的地は大阪、日程は2026年10月15日の"
            "日帰り、目的は顧客会議です。"
        )
    if "以下の内容で旅程を検索します" in text:
        return "OK"
    if "この旅程プランでよろしいですか" in text:
        return "オッケーです"
    if "申請を送信しますか" in text:
        return "お願いします"
    return None


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
            input=(
                "2026年10月15日に東京から大阪へ日帰りで"
                "顧客会議に行きたいです。"
            ),
            extra_body={"agent_session_id": conversation_id},
        )
        for _ in range(6):
            payload = response.model_dump(mode="json")
            if has_function_call(payload):
                print(
                    "Hosted Agent exposed an unsupported function call.",
                    file=sys.stderr,
                )
                print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
                return 1
            approvals = mcp_approval_requests(payload)
            if approvals:
                if len(approvals) != 1:
                    print(
                        "Hosted Agent returned multiple MCP approvals.",
                        file=sys.stderr,
                    )
                    return 1
                approval = approvals[0]
                if (
                    approval.get("name")
                    != "submit_travel_request_with_approval"
                ):
                    print(
                        "Hosted Agent requested approval for an unexpected tool.",
                        file=sys.stderr,
                    )
                    print(
                        json.dumps(approval, ensure_ascii=False),
                        file=sys.stderr,
                    )
                    return 1
                arguments = approval.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if set(arguments) != {"approval_id"}:
                    print(
                        "Submit approval arguments do not match the contract.",
                        file=sys.stderr,
                    )
                    print(
                        json.dumps(approval, ensure_ascii=False),
                        file=sys.stderr,
                    )
                    return 1
                if args.decision != "pending":
                    approved = args.decision == "approve"
                    response = openai_client.responses.create(
                        previous_response_id=response.id,
                        input=[
                            {
                                "type": "mcp_approval_response",
                                "approval_request_id": approval["id"],
                                "approve": approved,
                            }
                        ],
                        extra_body={"agent_session_id": conversation_id},
                    )
                    result = response.model_dump(mode="json")
                    if has_function_call(result) or mcp_approval_requests(result):
                        print(
                            "Hosted Agent returned an unexpected callback after "
                            "the approval decision.",
                            file=sys.stderr,
                        )
                        print(
                            json.dumps(result, ensure_ascii=False),
                            file=sys.stderr,
                        )
                        return 1
                    text = response_text(result)
                    if approved:
                        request_id = re.search(r"TR-[A-F0-9]{12}", text)
                        if not request_id:
                            print(
                                "Approved submission returned no request ID.",
                                file=sys.stderr,
                            )
                            print(
                                json.dumps(result, ensure_ascii=False),
                                file=sys.stderr,
                            )
                            return 1
                        print(
                            "Hosted Agent executed submit only after native "
                            f"MCP approval: {request_id.group(0)}"
                        )
                        return 0
                    if "キャンセル" not in text:
                        print(
                            "Rejected submission did not return cancellation.",
                            file=sys.stderr,
                        )
                        print(
                            json.dumps(result, ensure_ascii=False),
                            file=sys.stderr,
                        )
                        return 1
                    print(
                        "Hosted Agent rejected native MCP approval without "
                        "executing submit."
                    )
                    return 0
                print(
                    "Hosted Agent reached native MCP approval without "
                    "executing submit."
                )
                return 0

            text = response_text(payload)
            reply = next_reply(text)
            if reply is None:
                print(
                    "Hosted Agent returned an unexpected chat response.",
                    file=sys.stderr,
                )
                print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
                return 1
            response = openai_client.responses.create(
                previous_response_id=response.id,
                input=reply,
                extra_body={"agent_session_id": conversation_id},
            )
    finally:
        project_client.close()

    print("Hosted Agent did not reach native MCP approval.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
