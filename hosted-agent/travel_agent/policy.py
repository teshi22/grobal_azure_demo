"""Deterministic travel policy evaluation."""

from __future__ import annotations

from .models import TravelPlan


def evaluate_policy(plan: TravelPlan) -> dict[str, object]:
    details: list[str] = []
    compliant = True

    hotel_cost = plan.hotel_cost_per_night or 0
    hotel_limit = 12_000
    if hotel_cost > hotel_limit:
        details.append(
            f"❌ 宿泊費 ¥{hotel_cost:,}/泊 は上限 ¥{hotel_limit:,} を超過"
        )
        compliant = False
    elif hotel_cost:
        details.append(
            f"✅ 宿泊費 ¥{hotel_cost:,}/泊 は上限 ¥{hotel_limit:,} 以内"
        )
    else:
        details.append("✅ 宿泊なし（宿泊費チェック対象外）")

    if plan.trip_type == "宿泊":
        details.append("✅ 日当: 宿泊出張 5,000円/日")
    else:
        details.append("✅ 日当: 日帰り出張 3,000円/日")

    uses_taxi = any(
        "タクシー" in str(leg.get("method", "")) for leg in plan.transportation_legs
    )
    if uses_taxi:
        details.append("❌ タクシー利用: 原則禁止")
        compliant = False
    else:
        details.append("✅ タクシー利用なし")

    return {"compliant": compliant, "details": details}

