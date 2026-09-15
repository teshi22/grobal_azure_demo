from travel_agent.models import TravelPlan
from travel_agent.policy import evaluate_policy


def _plan(**overrides):
    data = {
        "departure": "大阪",
        "destination": "東京",
        "purpose": "顧客訪問",
        "schedule": "2026-09-10（木）",
        "trip_type": "日帰り",
        "transportation_legs": [],
        "transportation_cost": 20_000,
        "hotel": None,
        "hotel_cost_per_night": None,
        "hotel_nights": None,
        "total_cost": 23_000,
        "distance_km": 500,
        "travel_time_hours": 2.5,
    }
    data.update(overrides)
    return TravelPlan.model_validate(data)


def test_compliant_day_trip():
    result = evaluate_policy(_plan())
    assert result["compliant"] is True


def test_rejects_expensive_hotel_and_taxi():
    result = evaluate_policy(
        _plan(
            trip_type="宿泊",
            hotel="高額ホテル",
            hotel_cost_per_night=20_000,
            hotel_nights=1,
            transportation_legs=[
                {"method": "タクシー", "from": "駅", "to": "訪問先", "cost": 3000}
            ],
        )
    )
    assert result["compliant"] is False
    assert any("宿泊費" in detail for detail in result["details"])
    assert any("タクシー" in detail for detail in result["details"])


def test_rejects_taxi_from_live_planner_mode_field():
    result = evaluate_policy(
        _plan(
            transportation_legs=[
                {
                    "mode": "タクシー",
                    "departure_station": "駅",
                    "arrival_station": "訪問先",
                    "fare_jpy": 3000,
                }
            ]
        )
    )

    assert result["compliant"] is False
    assert any("タクシー" in detail for detail in result["details"])
