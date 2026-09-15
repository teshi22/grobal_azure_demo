"use client";

import {
  formatTransportationCost,
  normalizeTransportationLegs,
} from "@/lib/travel";
import type { CompletionData } from "@/lib/types";

interface Props {
  data: CompletionData;
}

export function CompletionCard({ data }: Props) {
  const plan = data.plan || {};
  const legs = normalizeTransportationLegs(plan.transportation_legs);
  const tripType = (plan.trip_type as string) || "日帰り";
  const destination = plan.destination as string | undefined;
  const schedule = plan.schedule as string | undefined;
  const purpose = plan.purpose as string | undefined;
  const hotel = plan.hotel as string | undefined;
  const transportCost = (plan.transportation_cost as number) || 0;
  const hotelCostPerNight = (plan.hotel_cost_per_night as number) || 0;
  const hotelNights = (plan.hotel_nights as number) || 0;
  const hotelTotal = hotelCostPerNight * hotelNights;
  const dailyAllowance =
    tripType === "宿泊" ? 5000 * (hotelNights + 1) : 3000;
  const grandTotal = transportCost + hotelTotal + dailyAllowance;

  const hasPlan = legs.length > 0 || destination;

  return (
    <div className="rounded-xl border-2 border-green-200 bg-gradient-to-b from-green-50 to-white p-5 shadow-sm">
      {/* ヘッダー */}
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-base font-semibold">✅ 出張申請完了</h3>
        {data.requestId && (
          <span className="rounded-full bg-green-100 px-3 py-1 text-xs font-medium text-green-700">
            申請番号: {data.requestId}
          </span>
        )}
      </div>

      {hasPlan ? (
        <>
          {/* 基本情報 */}
          <div className="mb-3 grid grid-cols-2 gap-2 text-sm">
            {destination && (
              <div>
                <span className="text-gray-500">出張先:</span>{" "}
                <span className="font-medium">{destination}</span>
              </div>
            )}
            {schedule && (
              <div>
                <span className="text-gray-500">日程:</span>{" "}
                <span className="font-medium">{schedule}</span>
              </div>
            )}
            {purpose && (
              <div>
                <span className="text-gray-500">目的:</span>{" "}
                <span className="font-medium">{purpose}</span>
              </div>
            )}
            <div>
              <span className="text-gray-500">種別:</span>{" "}
              <span className="font-medium">{tripType}</span>
            </div>
          </div>

          {/* 交通手段 */}
          {legs.length > 0 && (
            <div className="mb-3">
              <p className="mb-1 text-xs font-semibold text-gray-500">
                🚄 交通手段
              </p>
              <div className="space-y-1 text-sm">
                {legs.map((leg, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between rounded bg-gray-50 px-3 py-1"
                  >
                    <span>
                      {i + 1}. {leg.method}{" "}
                      <span className="text-gray-500">
                        {leg.from} → {leg.to}
                      </span>
                      {leg.fareType && (
                        <span className="ml-1 text-xs text-gray-500">
                          ({leg.fareType})
                        </span>
                      )}
                    </span>
                    <span className="font-medium whitespace-nowrap">
                      {formatTransportationCost(leg.cost)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 宿泊 */}
          {tripType === "宿泊" && hotel && (
            <div className="mb-3 text-sm">
              <p className="mb-1 text-xs font-semibold text-gray-500">
                🏨 宿泊
              </p>
              <div className="rounded bg-gray-50 px-3 py-1">
                {hotel} — {hotelNights}泊 ¥
                {hotelCostPerNight.toLocaleString()}/泊
              </div>
            </div>
          )}

          {/* 旅費規程チェック */}
          {data.policyDisplay && (
            <div className="mb-3 rounded-lg bg-green-50 px-4 py-2 text-sm text-green-800">
              <p className="mb-1 text-xs font-semibold">📋 旅費規程チェック: OK</p>
              {data.policyDisplay.split("\n").map((line, i) => (
                <p key={i} className="ml-2 text-xs">
                  {line}
                </p>
              ))}
            </div>
          )}

          {/* 費用サマリー */}
          <div className="rounded-lg bg-blue-50 px-4 py-3 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">交通費</span>
              <span>¥{transportCost.toLocaleString()}</span>
            </div>
            {hotelTotal > 0 && (
              <div className="flex justify-between">
                <span className="text-gray-600">宿泊費</span>
                <span>¥{hotelTotal.toLocaleString()}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-gray-600">日当</span>
              <span>¥{dailyAllowance.toLocaleString()}</span>
            </div>
            <div className="mt-1 flex justify-between border-t border-blue-200 pt-1 font-semibold text-blue-800">
              <span>合計</span>
              <span>¥{grandTotal.toLocaleString()}</span>
            </div>
          </div>
        </>
      ) : (
        /* プランデータがない場合はテキスト表示 */
        <pre className="whitespace-pre-wrap text-sm text-gray-700">
          {data.output}
        </pre>
      )}

      {/* 完了メッセージ */}
      <div className="mt-4 rounded-lg bg-green-100 px-4 py-2 text-center text-sm font-medium text-green-800">
        🎉 出張申請書の作成が完了し、申請システムへ送信しました！
        {data.requestId && `（申請番号: ${data.requestId}）`}
      </div>
    </div>
  );
}
