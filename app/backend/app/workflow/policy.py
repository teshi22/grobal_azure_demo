"""旅費規程チェックロジック (FunctionTool ハンドラ)"""


def evaluate_policy(args: dict) -> dict:
    """旅費規程に基づいて出張プランをチェックする

    PolicyChecker Agent の FunctionTool から呼ばれる。
    チェック項目: 宿泊費上限・日当・タクシー禁止
    """
    results: list[str] = []
    ok = True

    # 宿泊費上限チェック
    hotel = args.get("hotel_cost_per_night") or 0
    limit = 12000
    if hotel > limit:
        results.append(f"❌ 宿泊費 ¥{hotel:,}/泊 は上限 ¥{limit:,} を超過")
        ok = False
    elif hotel > 0:
        results.append(f"✅ 宿泊費 ¥{hotel:,}/泊 は上限 ¥{limit:,} 以内")
    else:
        results.append("✅ 宿泊なし（宿泊費チェック対象外）")

    # 日当チェック
    trip_type = args.get("trip_type", "日帰り")
    if trip_type == "宿泊":
        results.append("✅ 日当: 宿泊出張 5,000円/日")
    else:
        results.append("✅ 日当: 日帰り出張 3,000円/日")

    # タクシー利用チェック
    uses_taxi = args.get("uses_taxi", False)
    if uses_taxi:
        results.append("❌ タクシー利用: 原則禁止（深夜・早朝 22:00〜6:00 のみ例外）")
        ok = False
    else:
        results.append("✅ タクシー利用なし")

    return {"compliant": ok, "details": results}
