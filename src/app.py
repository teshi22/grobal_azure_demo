"""
出張申請エージェント — Streamlit チャット UI

使い方:
  streamlit run src/app.py

環境変数:
  AZURE_AI_PROJECT_ENDPOINT  - Foundry プロジェクトエンドポイント
  AGENT_NAME                 - ホステッドエージェント名 (default: travel-request-agent)
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests
import streamlit as st
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
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

# ---------------------------------------------------------------------------
# ページ設定
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="出張申請エージェント",
    page_icon="✈️",
    layout="centered",
)

st.title("✈️ 出張申請エージェント")
st.caption("Foundry Agent Service — ワークフロー型マルチエージェント")

# ---------------------------------------------------------------------------
# セッション状態の初期化
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "hitl_call_id" not in st.session_state:
    st.session_state.hitl_call_id = None
if "hitl_plan_text" not in st.session_state:
    st.session_state.hitl_plan_text = None
if "waiting" not in st.session_state:
    st.session_state.waiting = False


# ---------------------------------------------------------------------------
# Foundry クライアント (キャッシュ)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )


def get_openai_client():
    return get_client().get_openai_client()


def ensure_conversation() -> str:
    """会話が無ければ作成して ID を返す"""
    if st.session_state.conversation_id is None:
        openai = get_openai_client()
        conv = openai.conversations.create()
        st.session_state.conversation_id = conv.id
    return st.session_state.conversation_id


# ---------------------------------------------------------------------------
# エージェント呼び出し
# ---------------------------------------------------------------------------
def call_agent(input_data: Any, *, stream: bool = False) -> dict:
    """ホステッドエージェントを呼び出す"""
    conv_id = ensure_conversation()

    if not stream:
        openai = get_openai_client()
        resp = openai.responses.create(
            conversation=conv_id,
            extra_body={
                "agent_reference": {
                    "name": AGENT_NAME,
                    "type": "agent_reference",
                }
            },
            input=input_data,
        )
        return resp.model_dump()

    # ストリーミング (HITL 承認時に使用 — 100s タイムアウト回避)
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://ai.azure.com/.default"
    )
    token = token_provider()
    url = f"{PROJECT_ENDPOINT.rstrip('/')}/openai/v1/responses"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "input": input_data,
        "conversation": {"id": conv_id},
        "agent_reference": {"name": AGENT_NAME, "type": "agent_reference"},
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
# レスポンス解析
# ---------------------------------------------------------------------------
def extract_hitl(response: dict) -> tuple[str | None, str | None]:
    """HITL の call_id と表示テキストを抽出"""
    for item in response.get("output", []):
        if item.get("name") == "__hosted_agent_adapter_hitl__":
            return item.get("call_id"), item.get("arguments", "")
    return None, None


def extract_output_text(response: dict) -> str:
    """出力テキストを抽出"""
    texts: list[str] = []
    for item in response.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    texts.append(content.get("text", ""))
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# メッセージ処理
# ---------------------------------------------------------------------------
def process_user_input(user_text: str) -> None:
    """ユーザー入力を処理してエージェントを呼び出す"""
    st.session_state.messages.append({"role": "user", "content": user_text})

    hitl_call_id = st.session_state.hitl_call_id

    if hitl_call_id:
        # HITL 応答を処理
        st.session_state.hitl_call_id = None
        st.session_state.hitl_plan_text = None

        text_lower = user_text.strip().lower()
        is_approve = text_lower in ("ok", "yes", "y", "はい", "確定", "進めて", "大丈夫", "承認")

        if is_approve:
            input_data = [
                {
                    "call_id": hitl_call_id,
                    "output": json.dumps({"approved": True}),
                    "type": "function_call_output",
                }
            ]
        else:
            input_data = [
                {
                    "call_id": hitl_call_id,
                    "output": json.dumps({"approved": False, "feedback": user_text}),
                    "type": "function_call_output",
                }
            ]

        # HITL 応答後はストリーミング (タイムアウト回避)
        resp = call_agent(input_data, stream=True)
    else:
        # 通常リクエスト
        resp = call_agent(user_text)

    # レスポンス解析
    new_hitl_id, hitl_text = extract_hitl(resp)

    if new_hitl_id:
        # HITL → プラン確認
        st.session_state.hitl_call_id = new_hitl_id
        st.session_state.hitl_plan_text = hitl_text
        st.session_state.messages.append(
            {"role": "assistant", "content": hitl_text or "プランを確認してください。"}
        )
    else:
        # 通常応答 or 最終結果
        output = extract_output_text(resp)
        status = resp.get("status", "")
        error = resp.get("error")

        if error:
            msg = f"⚠️ エラーが発生しました: {error.get('message', str(error))}"
            st.session_state.messages.append({"role": "assistant", "content": msg})
        elif output:
            st.session_state.messages.append({"role": "assistant", "content": output})
        elif status == "completed":
            st.session_state.messages.append(
                {"role": "assistant", "content": "処理が完了しました。"}
            )
        else:
            st.session_state.messages.append(
                {"role": "assistant", "content": f"(ステータス: {status})"}
            )


# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("ℹ️ 使い方")
    st.markdown(
        """
1. 出張の内容を入力してください
2. エージェントが旅程プランを提案します
3. **OK** で承認 / 変更要望を入力
4. 規程チェック → 申請書作成 → 送信

**入力例:**
- `6/12に東京出張、顧客訪問`
- `4/5〜4/6に大阪本町で新規案件の提案`
"""
    )
    st.divider()
    if st.button("🔄 新しい会話を開始"):
        st.session_state.messages = []
        st.session_state.conversation_id = None
        st.session_state.hitl_call_id = None
        st.session_state.hitl_plan_text = None
        st.rerun()

    st.divider()
    st.caption(f"エージェント: `{AGENT_NAME}`")
    if st.session_state.conversation_id:
        st.caption(f"会話: `{st.session_state.conversation_id[:25]}...`")


# ---------------------------------------------------------------------------
# チャット表示
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# HITL 承認待ちの場合、ボタンを表示
if st.session_state.hitl_call_id:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ OK — このプランで進める", use_container_width=True, type="primary"):
            process_user_input("OK")
            st.rerun()
    with col2:
        pass  # 変更要望はチャット入力から

# ---------------------------------------------------------------------------
# チャット入力
# ---------------------------------------------------------------------------
if prompt := st.chat_input(
    "変更要望を入力..." if st.session_state.hitl_call_id else "出張の内容を入力してください..."
):
    with st.spinner("エージェント処理中..."):
        process_user_input(prompt)
    st.rerun()
