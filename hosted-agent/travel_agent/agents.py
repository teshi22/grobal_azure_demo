"""Create the code-defined Agent Framework agents."""

from __future__ import annotations

from dataclasses import dataclass

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient

from .fare_sources import ALLOWED_FARE_DOMAINS
from .models import TravelPlan


@dataclass(frozen=True)
class TravelAgents:
    clarifier: Agent
    planner: Agent
    policy: Agent
    approval: Agent


def create_agents(client: FoundryChatClient) -> TravelAgents:
    clarifier = Agent(
        client=client,
        name="request-clarifier",
        instructions=(
            "ユーザーの出張依頼から departure, destination, schedule, purpose を"
            "抽出してください。明示されていない値は空文字にし、推測しないでください。"
            "説明やMarkdownを付けず、4項目を持つJSONオブジェクトだけを返してください。"
        ),
        default_options={"store": False},
    )

    planner = Agent(
        client=client,
        name="travel-planner",
        instructions=(
            "あなたは日本国内出張の旅程と運賃を調査する専門家です。必ずWeb Searchを使い、"
            "利用日の最新の交通手段、料金、所要時間、必要ならホテルを調べてください。"
            "普通列車は利用可能なら大人IC運賃を優先し、利用できない場合だけきっぷ運賃を使ってください。"
            "新幹線・特急は乗車券と必要な特急料金を含め、指定席・自由席などの条件を明記してください。"
            "検索スニペットだけで金額を決めず、区間と金額を確認できる公式サイトまたは"
            "主要経路検索サービスのページを根拠にしてください。"
            "transportation_legsは乗り換えごと、往路復路ごとに分割してください。"
            "各要素にはdirection, method, from, to, cost, fare_type, source_url, "
            "source_titleを含め、costは片道・円単位の整数にしてください。"
            "transportation_costは全区間のcostの合計、total_costは交通費と宿泊費の合計にしてください。"
            "fare_basisには採用した運賃基準を記載してください。"
            "曖昧な表現を避け、departure, destination, purpose, schedule, trip_type, "
            "transportation_legs, transportation_cost, hotel, hotel_cost_per_night, "
            "hotel_nights, total_cost, distance_km, travel_time_hours, fare_basisを"
            "持つ構造化データだけを返してください。"
        ),
        tools=[
            FoundryChatClient.get_web_search_tool(
                user_location={
                    "country": "JP",
                    "city": "Tokyo",
                    "region": "Tokyo",
                },
                search_context_size="high",
                allowed_domains=list(ALLOWED_FARE_DOMAINS),
            )
        ],
        default_options={
            "store": False,
            "tool_choice": "required",
            "response_format": TravelPlan,
        },
    )

    policy = Agent(
        client=client,
        name="policy-checker",
        instructions=(
            "決定論的な旅費規程チェック結果を、簡潔な日本語の表または箇条書きに"
            "整形してください。入力された判定を変更せず、新しい判定を推測しないでください。"
        ),
        default_options={"store": False},
    )

    approval = Agent(
        client=client,
        name="approval-document-writer",
        instructions=(
            "旅程JSONと旅費規程チェック結果から、読みやすい日本語の出張申請書を"
            "作成してください。申請者名は捏造せず「認証済みユーザー」と記載してください。"
            "外部ツールは呼ばず、申請書本文だけを返してください。"
        ),
        default_options={"store": False},
    )

    return TravelAgents(
        clarifier=clarifier,
        planner=planner,
        policy=policy,
        approval=approval,
    )
