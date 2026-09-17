import pytest

from travel_agent.models import (
    PlanReviewResponse,
    RequestConfirmationResponse,
    TravelPlan,
    fare_evidence_errors,
)


@pytest.mark.parametrize(
    "reply",
    [
        "お願いします",
        "はい、お願いします。",
        "この内容でお願いします",
        "問題ありません",
    ],
)
def test_natural_affirmative_reply_is_accepted(reply):
    assert RequestConfirmationResponse.convert_from_payload(reply).confirmed
    assert PlanReviewResponse.convert_from_payload(reply).approved


def test_normalizes_verbose_day_trip_type():
    plan = TravelPlan.model_validate(
        {
            "departure": "東京",
            "destination": "大阪",
            "purpose": "顧客会議",
            "schedule": "2026-10-15（木）、日帰り",
            "trip_type": "往復・日帰り",
        }
    )

    assert plan.trip_type == "日帰り"


def test_normalizes_planner_cost_breakdown_and_measurements():
    plan = TravelPlan.model_validate(
        {
            "departure": "東京",
            "destination": "大阪",
            "purpose": "顧客会議",
            "schedule": "2026-10-15（木）、日帰り",
            "trip_type": "往復・日帰り",
            "transportation_cost": {
                "outbound_jpy": 14720,
                "return_jpy": 14720,
                "note": "検索結果に基づく",
            },
            "hotel_cost_per_night": "なし",
            "hotel_nights": "なし",
            "total_cost": "29,440円",
            "distance_km": "515 km",
            "travel_time_hours": "2.5時間",
        }
    )

    assert plan.transportation_cost == 29440
    assert plan.hotel_cost_per_night is None
    assert plan.hotel_nights is None
    assert plan.total_cost == 29440
    assert plan.distance_km == 515
    assert plan.travel_time_hours == 2.5


def test_normalizes_live_planner_transportation_leg_fields():
    plan = TravelPlan.model_validate(
        {
            "departure": "東京",
            "destination": "名古屋",
            "purpose": "顧客訪問",
            "schedule": "2026-10-16（金）日帰り",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "mode": "東海道新幹線",
                    "departure_station": "東京駅",
                    "arrival_station": "名古屋駅",
                    "fare_jpy": 11290,
                    "fare_type": "指定席",
                    "source_url": "https://smart-ex.jp/product/plan/service/",
                }
            ],
            "transportation_cost": 1,
            "total_cost": 1,
        }
    )

    assert plan.transportation_legs[0]["direction"] == "往路"
    assert plan.transportation_legs[0]["method"] == "東海道新幹線"
    assert plan.transportation_legs[0]["from"] == "東京駅"
    assert plan.transportation_legs[0]["to"] == "名古屋駅"
    assert plan.transportation_legs[0]["cost"] == 11290
    assert plan.transportation_cost == 11290
    assert plan.total_cost == 11290
    assert fare_evidence_errors(plan) == []


def test_preserves_leg_without_itemized_cost_as_unknown():
    plan = TravelPlan.model_validate(
        {
            "departure": "東京",
            "destination": "大阪",
            "purpose": "顧客会議",
            "schedule": "2026-10-15",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "segment": "往路",
                    "mode": "新幹線",
                    "departure_point": "東京駅",
                    "arrival_point": "新大阪駅",
                }
            ],
            "transportation_cost": 14720,
        }
    )

    assert plan.transportation_legs[0]["direction"] == "往路"
    assert plan.transportation_legs[0]["method"] == "新幹線"
    assert plan.transportation_legs[0]["from"] == "東京駅"
    assert plan.transportation_legs[0]["to"] == "新大阪駅"
    assert plan.transportation_legs[0]["cost"] is None
    assert fare_evidence_errors(plan) == [
        "transportation_legs[1].cost must be an integer fare",
        "transportation_legs[1].fare_type is required",
        (
            "transportation_legs[1].source_url must be an approved "
            "HTTPS fare source"
        ),
    ]


def test_rejects_unapproved_fare_source():
    plan = TravelPlan.model_validate(
        {
            "departure": "東京",
            "destination": "品川",
            "purpose": "会議",
            "schedule": "2026-09-09",
            "trip_type": "日帰り",
            "transportation_legs": [
                {
                    "direction": "往路",
                    "method": "JR東海道本線",
                    "from": "東京駅",
                    "to": "品川駅",
                    "cost": 209,
                    "fare_type": "IC",
                    "source_url": "https://example.com/fare",
                }
            ],
        }
    )

    assert fare_evidence_errors(plan) == [
        (
            "transportation_legs[1].source_url must be an approved "
            "HTTPS fare source"
        )
    ]
