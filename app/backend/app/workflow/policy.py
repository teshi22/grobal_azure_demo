"""旅費規程チェックロジック (FunctionTool ハンドラ)"""


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


def evaluate_policy(args: dict) -> dict:
    """旅費規程に基づいて出張プランをチェックする

    PolicyChecker Agent の FunctionTool から呼ばれる。
    """
    results: list[str] = []
    ok = True

    hotel = args.get("hotel_cost_per_night") or 0
    transport = args.get("transportation") or ""
    distance = args.get("distance_km") or 0
    travel_time = args.get("travel_time_hours") or 0

    limit = 12000
    if hotel > limit:
        results.append(f"❌ 宿泊費 ¥{hotel:,} は上限 ¥{limit:,} を超過")
        ok = False
    elif hotel > 0:
        results.append(f"✅ 宿泊費 ¥{hotel:,} は上限 ¥{limit:,} 以内")

    if "グリーン" in transport:
        if travel_time >= 3:
            results.append("✅ グリーン車: 乗車時間3時間超のため利用可")
        else:
            results.append("❌ グリーン車: 利用条件を満たしていません")
            ok = False

    if "飛行機" in transport or "航空" in transport:
        if distance >= 600:
            results.append(f"✅ 航空機: 片道 {distance}km ≥ 600km のため利用可")
        else:
            results.append(f"⚠️ 航空機: 片道 {distance}km < 600km のため要確認")

    if args.get("needs_pre_night_stay"):
        results.append("✅ 前泊: 始業時刻に間に合わない場合として申請")

    if not results:
        results.append("✅ 規程上の問題は見つかりませんでした")

    return {"compliant": ok, "details": results}
