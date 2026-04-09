#!/usr/bin/env python3
"""
ホステッドエージェント E2E テストスクリプト

使い方:
  # Foundry にデプロイ済みのホステッドエージェントをテスト
  python src/test_e2e.py

  # ローカルサーバー (localhost:8088) をテスト
  python src/test_e2e.py --local

環境変数:
  AZURE_AI_PROJECT_ENDPOINT  - Foundry プロジェクトエンドポイント
  AGENT_NAME                 - エージェント名 (default: travel-request-agent)
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
PROJECT_ENDPOINT = os.environ.get(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://travelpv4i.services.ai.azure.com/api/projects/travel-agent",
)
AGENT_NAME = os.environ.get("AGENT_NAME", "travel-request-agent")
LOCAL_MODE = "--local" in sys.argv
BASE_URL = "http://localhost:8088" if LOCAL_MODE else None


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def _green(s: str) -> str:
    return f"\033[92m{s}\033[0m"


def _red(s: str) -> str:
    return f"\033[91m{s}\033[0m"


def _yellow(s: str) -> str:
    return f"\033[93m{s}\033[0m"


def _header(s: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {s}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Foundry クライアント (リモートモード)
# ---------------------------------------------------------------------------
class FoundryClient:
    """Foundry Responses API 経由でホステッドエージェントを呼び出す"""

    def __init__(self, project_endpoint: str, agent_name: str) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        self.agent_name = agent_name
        self._client = AIProjectClient(
            endpoint=project_endpoint,
            credential=DefaultAzureCredential(),
        )
        self._openai = self._client.get_openai_client()

    def create_conversation(self) -> str:
        conv = self._openai.conversations.create()
        return conv.id

    def send_message(self, conversation_id: str, input_data: Any) -> dict:
        """非ストリーミングでリクエストを送信し、レスポンス辞書を返す"""
        response = self._openai.responses.create(
            conversation=conversation_id,
            extra_body={
                "agent_reference": {
                    "name": self.agent_name,
                    "type": "agent_reference",
                }
            },
            input=input_data,
        )
        return response.model_dump()

    def send_message_stream(self, conversation_id: str, input_data: Any) -> dict:
        """ストリーミングでリクエストを送信し、最終レスポンスを返す"""
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://ai.azure.com/.default"
        )
        token = token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = f"{self._client._config.endpoint.rstrip('/')}/openai/v1/responses"

        body: dict[str, Any] = {
            "input": input_data,
            "conversation": {"id": conversation_id},
            "agent_reference": {
                "name": self.agent_name,
                "type": "agent_reference",
            },
            "stream": True,
        }

        resp = requests.post(url, headers=headers, json=body, stream=True, timeout=300)
        resp.raise_for_status()

        result: dict = {}
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue
            data = decoded[6:]
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                if event.get("type") == "response.completed":
                    result = event.get("response", {})
            except json.JSONDecodeError:
                pass

        return result


# ---------------------------------------------------------------------------
# ローカルクライアント
# ---------------------------------------------------------------------------
class LocalClient:
    """localhost:8088 の hosted.py サーバーを直接呼び出す"""

    def __init__(self, base_url: str = "http://localhost:8088") -> None:
        self.base_url = base_url.rstrip("/")

    def create_conversation(self) -> str:
        import secrets
        import string

        chars = string.ascii_letters + string.digits
        return "conv_" + "".join(secrets.choice(chars) for _ in range(50))

    def send_message(self, conversation_id: str, input_data: Any) -> dict:
        url = f"{self.base_url}/responses"
        body: dict[str, Any] = {
            "input": input_data,
            "conversation": {"id": conversation_id},
            "stream": False,
        }
        resp = requests.post(url, json=body, timeout=300)
        resp.raise_for_status()
        return resp.json()

    def send_message_stream(self, conversation_id: str, input_data: Any) -> dict:
        url = f"{self.base_url}/responses"
        body: dict[str, Any] = {
            "input": input_data,
            "conversation": {"id": conversation_id},
            "stream": True,
        }
        resp = requests.post(url, json=body, stream=True, timeout=300)
        resp.raise_for_status()

        result: dict = {}
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue
            data = decoded[6:]
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                if event.get("type") == "response.completed":
                    result = event.get("response", {})
            except json.JSONDecodeError:
                pass

        return result


# ---------------------------------------------------------------------------
# HITL ヘルパー
# ---------------------------------------------------------------------------
def extract_hitl(response: dict) -> tuple[str | None, str | None]:
    """レスポンスから HITL の call_id と表示テキストを抽出"""
    for item in response.get("output", []):
        if item.get("name") == "__hosted_agent_adapter_hitl__":
            return item.get("call_id"), item.get("arguments", "")
    return None, None


def make_hitl_approve(call_id: str) -> list[dict]:
    """HITL 承認用の function_call_output を作成"""
    return [
        {
            "call_id": call_id,
            "output": json.dumps({"approved": True}),
            "type": "function_call_output",
        }
    ]


def make_hitl_reject(call_id: str, feedback: str) -> list[dict]:
    """HITL 却下用の function_call_output を作成"""
    return [
        {
            "call_id": call_id,
            "output": json.dumps({"approved": False, "feedback": feedback}),
            "type": "function_call_output",
        }
    ]


# ---------------------------------------------------------------------------
# テストケース
# ---------------------------------------------------------------------------
def test_day_trip(client: FoundryClient | LocalClient) -> bool:
    """日帰り出張 → HITL承認 → 申請完了"""
    _header("テスト 1: 日帰り出張 (東京)")

    conv_id = client.create_conversation()
    print(f"  会話ID: {conv_id[:30]}...")

    # Step 1: 出張リクエスト
    print(f"\n  {_yellow('Step 1')}: 出張リクエスト送信中...")
    t0 = time.time()
    resp1 = client.send_message(conv_id, "6/12に東京出張、顧客訪問")
    elapsed = time.time() - t0
    print(f"  ステータス: {resp1.get('status')} ({elapsed:.1f}s)")

    hitl_call_id, hitl_text = extract_hitl(resp1)
    if not hitl_call_id:
        print(f"  {_red('FAIL')}: HITL が返されませんでした")
        return False

    print(f"  {_green('HITL 検出')}: call_id={hitl_call_id[:20]}...")
    print(f"  プラン:\n{_indent(hitl_text or '', 4)}")

    # Step 2: HITL 承認 (ストリーミング)
    print(f"\n  {_yellow('Step 2')}: HITL 承認送信中 (stream)...")
    t0 = time.time()
    resp2 = client.send_message_stream(conv_id, make_hitl_approve(hitl_call_id))
    elapsed = time.time() - t0
    status = resp2.get("status", "unknown")
    print(f"  ステータス: {status} ({elapsed:.1f}s)")

    # 結果確認
    output_text = _extract_output_text(resp2)
    if status == "completed" and output_text:
        print(f"  {_green('出力')}:\n{_indent(output_text, 4)}")
        print(f"\n  {_green('✅ テスト 1 PASSED')}")
        return True
    else:
        print(f"  {_red('❌ テスト 1 FAILED')}: status={status}")
        if resp2.get("error"):
            print(f"  エラー: {resp2['error']}")
        return False


def test_overnight_trip(client: FoundryClient | LocalClient) -> bool:
    """宿泊出張 → HITL承認 → 申請完了"""
    _header("テスト 2: 宿泊出張 (大阪)")

    conv_id = client.create_conversation()
    print(f"  会話ID: {conv_id[:30]}...")

    # Step 1
    print(f"\n  {_yellow('Step 1')}: 出張リクエスト送信中...")
    t0 = time.time()
    resp1 = client.send_message(
        conv_id,
        "4/5〜4/6に大阪のお客様先（本町駅周辺）を訪問。目的は新規案件の提案。",
    )
    elapsed = time.time() - t0
    print(f"  ステータス: {resp1.get('status')} ({elapsed:.1f}s)")

    hitl_call_id, hitl_text = extract_hitl(resp1)
    if not hitl_call_id:
        print(f"  {_red('FAIL')}: HITL が返されませんでした")
        return False

    print(f"  {_green('HITL 検出')}: call_id={hitl_call_id[:20]}...")
    print(f"  プラン:\n{_indent(hitl_text or '', 4)}")

    # Step 2: 承認
    print(f"\n  {_yellow('Step 2')}: HITL 承認送信中 (stream)...")
    t0 = time.time()
    resp2 = client.send_message_stream(conv_id, make_hitl_approve(hitl_call_id))
    elapsed = time.time() - t0
    status = resp2.get("status", "unknown")
    print(f"  ステータス: {status} ({elapsed:.1f}s)")

    output_text = _extract_output_text(resp2)
    if status == "completed" and output_text:
        print(f"  {_green('出力')}:\n{_indent(output_text, 4)}")
        print(f"\n  {_green('✅ テスト 2 PASSED')}")
        return True
    else:
        print(f"  {_red('❌ テスト 2 FAILED')}: status={status}")
        return False


def test_hitl_reject(client: FoundryClient | LocalClient) -> bool:
    """HITL 却下 → プラン再作成"""
    _header("テスト 3: HITL 却下 → プラン変更")

    conv_id = client.create_conversation()
    print(f"  会話ID: {conv_id[:30]}...")

    # Step 1
    print(f"\n  {_yellow('Step 1')}: 出張リクエスト送信中...")
    resp1 = client.send_message(conv_id, "6/12に東京出張、顧客訪問")
    print(f"  ステータス: {resp1.get('status')}")

    hitl_call_id, _ = extract_hitl(resp1)
    if not hitl_call_id:
        print(f"  {_red('FAIL')}: HITL が返されませんでした")
        return False

    # Step 2: 却下（変更要望）
    print(f"\n  {_yellow('Step 2')}: HITL 却下送信中...")
    t0 = time.time()
    resp2 = client.send_message_stream(
        conv_id,
        make_hitl_reject(hitl_call_id, "飛行機で行きたい"),
    )
    elapsed = time.time() - t0
    status = resp2.get("status", "unknown")
    print(f"  ステータス: {status} ({elapsed:.1f}s)")

    # 却下後は新しい HITL が返されるはず
    hitl_call_id2, hitl_text2 = extract_hitl(resp2)
    if hitl_call_id2:
        print(f"  {_green('新しい HITL 検出')}: call_id={hitl_call_id2[:20]}...")
        print(f"  新プラン:\n{_indent(hitl_text2 or '', 4)}")
        print(f"\n  {_green('✅ テスト 3 PASSED')}")
        return True
    elif status == "completed":
        output_text = _extract_output_text(resp2)
        print(f"  出力: {output_text[:200] if output_text else '(empty)'}")
        print(f"\n  {_yellow('⚠️ テスト 3 PARTIAL')}: HITL なしで完了")
        return True
    else:
        print(f"  {_red('❌ テスト 3 FAILED')}: status={status}")
        return False


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------
def _extract_output_text(resp: dict) -> str:
    """レスポンスから出力テキストを抽出"""
    texts: list[str] = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    texts.append(content.get("text", ""))
        elif item.get("type") == "function_call":
            args = item.get("arguments", "")
            if args and item.get("name") != "__hosted_agent_adapter_hitl__":
                texts.append(f"[{item.get('name')}] {args[:200]}")
    return "\n".join(texts)


def _indent(text: str, n: int) -> str:
    prefix = " " * n
    return "\n".join(prefix + line for line in text.split("\n"))


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> None:
    mode = "ローカル" if LOCAL_MODE else "Foundry"
    _header(f"ホステッドエージェント E2E テスト ({mode})")

    if LOCAL_MODE:
        client: FoundryClient | LocalClient = LocalClient(BASE_URL or "http://localhost:8088")
        print(f"  サーバー: {BASE_URL or 'http://localhost:8088'}")
    else:
        client = FoundryClient(PROJECT_ENDPOINT, AGENT_NAME)
        print(f"  エンドポイント: {PROJECT_ENDPOINT}")
        print(f"  エージェント: {AGENT_NAME}")

    results: list[tuple[str, bool]] = []

    # テスト実行
    tests = [
        ("日帰り出張", test_day_trip),
        ("宿泊出張", test_overnight_trip),
        ("HITL却下", test_hitl_reject),
    ]

    for name, test_fn in tests:
        try:
            passed = test_fn(client)
            results.append((name, passed))
        except Exception as e:
            print(f"\n  {_red(f'❌ {name} ERROR')}: {e}")
            results.append((name, False))

    # サマリー
    _header("テスト結果サマリー")
    passed_count = sum(1 for _, p in results if p)
    total = len(results)
    for name, passed in results:
        icon = _green("✅") if passed else _red("❌")
        print(f"  {icon} {name}")
    print(f"\n  {passed_count}/{total} passed")

    sys.exit(0 if passed_count == total else 1)


if __name__ == "__main__":
    main()
