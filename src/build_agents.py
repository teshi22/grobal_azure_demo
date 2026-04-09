# =============================================================================
# Foundry Agent ビルドスクリプト
# =============================================================================
# 3 つの Foundry Agent を作成し、エージェント名を .env に保存する。
# ワークフロー実行前に一度だけ実行すればよい。
#
# 使い方:
#   python src/build_agents.py          # 作成
#   python src/build_agents.py --delete  # 削除
#   python src/build_agents.py --list    # 一覧
# =============================================================================

import argparse
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    BingGroundingSearchConfiguration,
    BingGroundingSearchToolParameters,
    BingGroundingTool,
    FunctionTool,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv, set_key

load_dotenv()

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
MODEL = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
BING_CONNECTION_ID = os.environ.get("BING_PROJECT_CONNECTION_ID", "")
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

# ---------------------------------------------------------------------------
# 社内旅費規程
# ---------------------------------------------------------------------------
TRAVEL_POLICY = """
【社内旅費規程】
1. 宿泊費上限: 12,000円/泊
2. 交通手段: 新幹線普通車・指定席を原則とする
3. グリーン車: 乗車時間3時間超の場合に限り利用可
4. 航空機利用: 片道600km以上、または新幹線より安価な場合に利用可
5. 前泊: 始業時刻(9:00)に間に合わない場合に認められる
6. 日当: 国内出張 2,500円/日、海外出張 5,000円/日
7. タクシー: 原則禁止。深夜・早朝(22:00〜6:00)または荷物が多い場合のみ可
"""

# ---------------------------------------------------------------------------
# エージェント定義
# ---------------------------------------------------------------------------
AGENTS = [
    {
        "name": "RequestClarifier",
        "env_key": "REQUEST_CLARIFIER_AGENT",
        "description": "出張リクエスト情報確認エージェント",
        "instructions": """あなたは出張申請の受付担当です。
ユーザーの出張リクエストを分析し、申請に必要な情報が揃っているか確認してください。

【必須項目】
1. 出発地（未記載の場合は「大阪」と仮定してよい）
2. 目的地（具体的なエリアまで）
3. 日程（訪問日が 1 日でもあれば十分。「6/12」のような単日指定は日帰り出張として扱う。期間指定も可）
4. 出張目的

【判定の注意】
- 日付が 1 つでも書いてあれば「日程」は充足とみなすこと（例: 「6/12」→ OK）
- 出張目的が未記載でも、目的地が大学・企業名等であれば「訪問」と推定して complete: true にしてよい
- 厳しく不足判定しすぎないこと。常識的に推定できる項目は補完する

【出力ルール】
必ず以下の JSON 形式のみで回答してください。説明文は不要。

情報が十分な場合:
```json
{"complete": true, "missing_fields": [], "question": "", "enriched_request": "全情報を含む整理されたリクエスト文"}
```

情報が不足している場合:
```json
{"complete": false, "missing_fields": ["不足フィールド名"], "question": "ユーザーへの質問（日本語、丁寧語、1つの質問に絞る）", "enriched_request": ""}
```
""",
        "tools_factory": lambda: [],
    },
    {
        "name": "TravelPlanner",
        "env_key": "TRAVEL_PLANNER_AGENT",
        "description": "旅程検索・提案エージェント (Bing Grounding 付き)",
        "instructions": """あなたは出張の旅程を検索・提案する専門エージェントです。

ユーザーの出張リクエスト(目的地、日程、目的)を受け取り、以下を検索・提案してください:
1. 交通手段（新幹線・飛行機の時刻・料金）
2. 宿泊が必要な場合は宿泊先（出張先周辺のビジネスホテル、料金）
3. 最適なプランの提案（所要時間・コスト比較）

【日帰り / 宿泊の判定ルール】
- 日程が 1 日のみ（例: 4/6）→ 日帰り（trip_type: "日帰り"）
- 日程が期間（例: 4/5〜4/6）→ 宿泊（trip_type: "宿泊"）
- 日帰りの場合、hotel / hotel_cost_per_night / hotel_nights は不要（null にする）

【重要ルール】
- 必ず具体的な金額・数値を含めること（概算で構いません）
- 「確認が必要」「未確定」などの曖昧な表現は禁止
- 交通手段は区間ごとに分けて記載すること（往路・復路それぞれ）
- 出力は以下の JSON 形式のみ。説明文は不要。

```json
{
  "trip_type": "日帰り or 宿泊",
  "transportation_legs": [
    {"method": "交通手段（例: 新幹線のぞみ 普通車指定席）", "from": "出発駅", "to": "到着駅", "cost": 片道金額（円、整数）},
    {"method": "交通手段", "from": "出発駅", "to": "到着駅", "cost": 片道金額（円、整数）}
  ],
  "transportation_cost": 交通費合計（円、整数）,
  "hotel": "ホテル名 or null（日帰りの場合）",
  "hotel_cost_per_night": 1泊料金 or null（円、整数）,
  "hotel_nights": 泊数 or null（整数）,
  "schedule": "スケジュール概要",
  "total_cost": 合計金額（円、整数）,
  "distance_km": 片道距離（km、数値）,
  "travel_time_hours": 片道所要時間（時間、数値）
}
```
""",
        "tools_factory": lambda: (
            [
                BingGroundingTool(
                    bing_grounding=BingGroundingSearchToolParameters(
                        search_configurations=[
                            BingGroundingSearchConfiguration(
                                project_connection_id=BING_CONNECTION_ID
                            )
                        ]
                    )
                )
            ]
            if BING_CONNECTION_ID
            else []
        ),
    },
    {
        "name": "PolicyChecker",
        "env_key": "POLICY_CHECKER_AGENT",
        "description": "旅費規程チェックエージェント (FunctionTool 付き)",
        "instructions": f"""あなたは社内旅費規程のチェックを行う専門エージェントです。

以下の社内旅費規程に基づいて、提案された出張プランが規程に適合しているか確認してください。
check_travel_policy ツールを使って判定を行ってください。

{TRAVEL_POLICY}

出力は以下を含めてください:
- 各項目の適合/不適合の判定結果
- 不適合の場合、代替案の提案
""",
        "tools_factory": lambda: [
            FunctionTool(
                name="check_travel_policy",
                parameters={
                    "type": "object",
                    "properties": {
                        "transportation": {
                            "type": "string",
                            "description": "交通手段 (例: 新幹線指定席, 新幹線グリーン車, 飛行機)",
                        },
                        "hotel_cost_per_night": {
                            "type": "number",
                            "description": "宿泊費（1泊あたり、円）",
                        },
                        "needs_pre_night_stay": {
                            "type": "boolean",
                            "description": "前泊が必要か",
                        },

                        "distance_km": {
                            "type": "number",
                            "description": "片道距離(km)",
                        },
                        "travel_time_hours": {
                            "type": "number",
                            "description": "片道所要時間(時間)",
                        },
                    },
                    "required": [
                        "transportation",
                        "hotel_cost_per_night",
                    ],
                    "additionalProperties": False,
                },
                description="出張プランが社内旅費規程に適合しているかチェックする",
                strict=False,
            ),
        ],
    },
    {
        "name": "ApprovalAgent",
        "env_key": "APPROVAL_AGENT",
        "description": "出張申請書作成エージェント",
        "instructions": """あなたは出張申請書を作成する専門エージェントです。

旅程情報と規程チェック結果を受け取り、以下の形式で出張申請書を作成してください:

===== 出張申請書 =====
■ 申請者: （ユーザー情報から）
■ 出張先:
■ 出張期間:
■ 目的:
■ 交通手段:
  （区間ごとに記載）
  1. [交通手段] [出発駅] → [到着駅] ¥XX,XXX
  2. [交通手段] [出発駅] → [到着駅] ¥XX,XXX
■ 種別: 日帰り or 宿泊
■ 宿泊先: （宿泊の場合のみ記載。日帰りは「なし」）
■ 旅費規程チェック: OK / NG
■ 概算費用:
  - 交通費: ¥XX,XXX
  - 宿泊費: ¥XX,XXX（日帰りの場合は ¥0）
  - 日当:   ¥XX,XXX
  - 合計:   ¥XX,XXX
========================

最後に「✅ 申請書の作成が完了しました。申請システムへの送信確認をお願いします。」と表示してください。
""",
        "tools_factory": lambda: [],
    },
]


# ---------------------------------------------------------------------------
# コマンド: 作成
# ---------------------------------------------------------------------------


def build_agents():
    """Foundry Agent を作成し、エージェント名を .env に保存"""
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

    print("=" * 60)
    print("🔨 Foundry Agent ビルド")
    print("=" * 60)
    print(f"  Endpoint: {PROJECT_ENDPOINT}")
    print(f"  Model:    {MODEL}")
    print()

    for agent_def in AGENTS:
        name = agent_def["name"]
        print(f"📦 {name} を作成中...")

        agent = project_client.agents.create_version(
            agent_name=name,
            definition=PromptAgentDefinition(
                model=MODEL,
                instructions=agent_def["instructions"],
                tools=agent_def["tools_factory"](),
            ),
        )

        # .env にエージェント名を保存
        set_key(ENV_FILE, agent_def["env_key"], agent.name)

        print(f"  ✅ name={agent.name}, version={agent.version}")
        print(f"     {agent_def['description']}")
        print(f"     → .env: {agent_def['env_key']}={agent.name}")
        print()

    print("=" * 60)
    print("✅ 全エージェント作成完了！")
    print(f"   .env に保存済み: {ENV_FILE}")
    print()
    print("ワークフロー実行:")
    print("  python src/workflow.py")


# ---------------------------------------------------------------------------
# コマンド: 削除
# ---------------------------------------------------------------------------


def delete_agents():
    """Foundry Agent を削除"""
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

    print("🗑️  Foundry Agent 削除")
    print()

    for agent_def in AGENTS:
        name = os.environ.get(agent_def["env_key"], agent_def["name"])
        try:
            # バージョン一覧を取得して全削除
            versions = project_client.agents.list_versions(agent_name=name)
            count = 0
            for v in versions:
                try:
                    project_client.agents.delete_version(
                        agent_name=v.name, agent_version=v.version
                    )
                    count += 1
                except Exception:
                    pass
            if count > 0:
                print(f"  ✅ {name}: {count} バージョン削除")
            else:
                print(f"  ⏭️  {name}: バージョンなし")
        except Exception as e:
            print(f"  ⚠️  {name}: {e}")

    # .env からエージェント名を削除
    for agent_def in AGENTS:
        try:
            set_key(ENV_FILE, agent_def["env_key"], "")
        except Exception:
            pass

    print()
    print("✅ 削除完了")


# ---------------------------------------------------------------------------
# コマンド: 一覧
# ---------------------------------------------------------------------------


def list_agents():
    """Foundry Agent の一覧を表示"""
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

    print("📋 Foundry Agent 一覧")
    print()

    for agent_def in AGENTS:
        name = os.environ.get(agent_def["env_key"], agent_def["name"])
        try:
            versions = list(project_client.agents.list_versions(agent_name=name))
            if versions:
                for v in versions:
                    tools_count = len(v.definition.tools) if hasattr(v.definition, "tools") and v.definition.tools else 0
                    print(f"  ✅ {v.name} (version={v.version}, model={v.definition.model}, tools={tools_count})")
            else:
                print(f"  ❌ {name}: 未作成")
        except Exception:
            print(f"  ❌ {name}: 未作成")

    print()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Foundry Agent ビルドツール")
    parser.add_argument("--delete", action="store_true", help="エージェントを削除")
    parser.add_argument("--list", action="store_true", help="エージェント一覧を表示")
    args = parser.parse_args()

    if args.delete:
        delete_agents()
    elif args.list:
        list_agents()
    else:
        build_agents()
