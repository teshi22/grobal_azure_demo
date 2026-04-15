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
LOCAL_SERVER_URL = os.environ.get("LOCAL_SERVER_URL", "")  # 設定時はローカルサーバーを使用

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
if "hitl_type" not in st.session_state:
    st.session_state.hitl_type = None  # "clarification" or "plan_review"
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
        if LOCAL_SERVER_URL:
            import secrets as _sec, string as _str
            chars = _str.ascii_letters + _str.digits
            st.session_state.conversation_id = "conv_" + "".join(
                _sec.choice(chars) for _ in range(50)
            )
        else:
            openai = get_openai_client()
            conv = openai.conversations.create()
            st.session_state.conversation_id = conv.id
    return st.session_state.conversation_id


# ---------------------------------------------------------------------------
# エージェント呼び出し
# ---------------------------------------------------------------------------
def _call_local_server(input_data: Any, conv_id: str) -> dict:
    """ローカルサーバー (hosted.py) を直接呼び出す"""
    body: dict[str, Any] = {
        "input": input_data,
        "conversation": {"id": conv_id},
        "stream": True,
    }
    resp = requests.post(
        f"{LOCAL_SERVER_URL.rstrip('/')}/responses",
        json=body,
        stream=True,
        timeout=300,
    )
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
            etype = event.get("type", "")
            print(f"[SSE] {etype}")
            if etype == "response.completed":
                result = event.get("response", {})
                print(f"[SSE] completed: status={result.get('status')} output_count={len(result.get('output', []))}")
                for i, item in enumerate(result.get("output", [])):
                    print(f"[SSE]   output[{i}] type={item.get('type')} name={item.get('name','')}")
        except json.JSONDecodeError:
            pass
    print(f"[SSE] returning result with {len(result.get('output', []))} output items")
    return result


def call_agent(input_data: Any, *, stream: bool = False) -> dict:
    """エージェントを呼び出す (ローカル or ホステッド)"""
    conv_id = ensure_conversation()

    if LOCAL_SERVER_URL:
        return _call_local_server(input_data, conv_id)

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
def extract_hitl(response: dict) -> tuple[str | None, str | None, str | None]:
    """HITL の call_id, 表示テキスト, 種別 を抽出。

    種別:
      - "clarification": 情報不足 → ユーザーに追加質問
      - "plan_review": プラン確認 → 承認 or 変更要望
    """
    for item in response.get("output", []):
        name = item.get("name", "")
        if name not in ("__hosted_agent_adapter_hitl__", "request_info"):
            continue
        call_id = item.get("call_id")
        args_str = item.get("arguments", "")
        # 引数を解析して種別を判定
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            args = {}
        # ClarificationHITLRequest かどうか判定
        is_clarification = (
            "missing_fields" in args
            or "question" in args
            or "ClarificationHITL" in args_str
        )
        if is_clarification:
            # 表示用テキストを組み立て
            question = args.get("question", "")
            missing = args.get("missing_fields", [])
            if not question and "ClarificationHITL" in args_str:
                # ローカルサーバーの repr 形式をパース
                import re
                m = re.search(r"question='([^']*)'", args_str)
                question = m.group(1) if m else "追加情報を教えてください。"
                m2 = re.search(r"missing_fields=\[([^\]]*)\]", args_str)
                if m2:
                    missing = [f.strip().strip("'\"") for f in m2.group(1).split(",") if f.strip()]
            display = "❓ 情報が不足しています\n\n"
            if missing:
                display += f"不足項目: {'、'.join(missing)}\n\n"
            display += question
            return call_id, display, "clarification"
        # plan_review: プランテキストを整形表示
        display = _format_plan_review(args_str, args)
        return call_id, display, "plan_review"
    return None, None, None


def _format_plan_review(args_str: str, args: dict) -> str:
    """PlanReviewRequest の表示テキストを生成する"""
    import re as _re

    # ホステッド: args は JSON {"plan_text": "..."}
    plan_text = args.get("plan_text", "")

    # ローカル: args_str は repr 形式 "PlanReviewRequest(plan_text='...')"
    if not plan_text and "PlanReviewRequest" in args_str:
        m = _re.search(r"plan_text='(.*)'", args_str, _re.DOTALL)
        if m:
            plan_text = m.group(1).replace("\\n", "\n").replace("\\'", "'")

    if not plan_text:
        return args_str  # フォールバック

    # JSON プランをフォーマット
    try:
        plan = json.loads(plan_text)
        trip_type = plan.get("trip_type", "宿泊")

        # --- 入力内容の確認 ---
        header = "✅ 入力内容を確認しました\n\n"
        schedule = plan.get("schedule", "")
        departure = plan.get("departure", "")
        destination = plan.get("destination", "")
        purpose = plan.get("purpose", "")
        items: list[str] = []
        if schedule:
            items.append(f"📅 日程: {schedule}")
        if departure:
            items.append(f"📍 出発地: {departure}")
        if destination:
            items.append(f"📍 目的地: {destination}")
        if purpose:
            items.append(f"🎯 目的: {purpose}")
        items.append(f"🏷️ 種別: {trip_type}")
        header += "  \n".join(items)

        # --- 旅程プラン ---
        parts: list[str] = [header + "\n\n---\n\n📋 旅程プラン\n"]

        for i, leg in enumerate(plan.get("transportation_legs", []), 1):
            parts.append(
                f"{i}. {leg.get('method', '—')}  "
                f"{leg.get('from', '—')} → {leg.get('to', '—')}  "
                f"¥{leg.get('cost', 0):,}"
            )

        # リストの後に空行を入れて段落を分ける
        summary = f"\n\n交通費計: ¥{plan.get('transportation_cost', 0):,}  \n"
        if trip_type == "宿泊":
            nights = plan.get("hotel_nights", 1)
            summary += (
                f"宿泊先: {plan.get('hotel', '—')} "
                f"¥{plan.get('hotel_cost_per_night', 0):,}/泊 × {nights}泊  \n"
            )
        else:
            summary += "宿泊: なし（日帰り）  \n"
        summary += f"**合計: ¥{plan.get('total_cost', 0):,}**"
        parts.append(summary)

        parts.append("\nこのプランでよろしいですか？\n→ 「OK」で確定 / 変更要望をテキストで入力")
        return "\n".join(parts)
    except (json.JSONDecodeError, TypeError):
        return f"{plan_text}\n\nこのプランでよろしいですか？（OK / 変更要望を入力）"


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
    hitl_type = st.session_state.hitl_type

    if hitl_call_id:
        # HITL 応答を処理
        st.session_state.hitl_call_id = None
        st.session_state.hitl_plan_text = None
        st.session_state.hitl_type = None

        if hitl_type == "clarification":
            # 情報不足への追加回答
            input_data = [
                {
                    "call_id": hitl_call_id,
                    "output": json.dumps({"answer": user_text}),
                    "type": "function_call_output",
                }
            ]
        else:
            # プラン確認の承認/変更要望
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
    new_hitl_id, hitl_text, new_hitl_type = extract_hitl(resp)

    if new_hitl_id:
        st.session_state.hitl_call_id = new_hitl_id
        st.session_state.hitl_plan_text = hitl_text
        st.session_state.hitl_type = new_hitl_type
        st.session_state.messages.append(
            {"role": "assistant", "content": hitl_text or "確認してください。"}
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
        st.session_state.hitl_type = None
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

# HITL 承認待ちの場合、種別に応じたボタンを表示
if st.session_state.hitl_call_id:
    if st.session_state.hitl_type == "plan_review":
        btn_key = f"ok_btn_{st.session_state.hitl_call_id}"
        if st.button("✅ OK — このプランで進める", use_container_width=True, type="primary", key=btn_key):
            process_user_input("OK")
            st.rerun()
    # clarification の場合はチャット入力のみ（ボタン不要）

# ---------------------------------------------------------------------------
# チャット入力
# ---------------------------------------------------------------------------
if st.session_state.hitl_type == "clarification":
    placeholder = "追加情報を入力..."
elif st.session_state.hitl_type == "plan_review":
    placeholder = "変更要望を入力..."
else:
    placeholder = "出張の内容を入力してください..."

if prompt := st.chat_input(placeholder):
    with st.spinner("エージェント処理中..."):
        process_user_input(prompt)
    st.rerun()
