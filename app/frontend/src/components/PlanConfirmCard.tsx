"use client";

import {
  formatTransportationCost,
  normalizeTransportationLegs,
} from "@/lib/travel";
import type { TransportationLeg } from "@/lib/types";

interface Props {
  data: Record<string, unknown>;
  disabled?: boolean;
  onConfirm: () => void;
  onRevise: (feedback: string) => void;
}

function splitLegs(legs: TransportationLeg[]): {
  outbound: TransportationLeg[];
  returnTrip: TransportationLeg[];
} {
  const outbound = legs.filter((leg) => leg.direction?.includes("往"));
  const returnTrip = legs.filter((leg) => leg.direction?.includes("復"));
  if (outbound.length + returnTrip.length === legs.length) {
    return { outbound, returnTrip };
  }
  if (legs.length < 2) return { outbound: legs, returnTrip: [] };
  const mid = Math.ceil(legs.length / 2);
  return { outbound: legs.slice(0, mid), returnTrip: legs.slice(mid) };
}

function LegTable({
  label,
  legs,
  startIndex,
}: {
  label: string;
  legs: TransportationLeg[];
  startIndex: number;
}) {
  const costs = legs.flatMap((leg) =>
    typeof leg.cost === "number" ? [leg.cost] : [],
  );
  const subtotal = costs.reduce((sum, cost) => sum + cost, 0);
  return (
    <div>
      <p className="mb-1 text-xs font-semibold text-gray-500">{label}</p>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-gray-500">
            <th className="py-1 pr-2 font-medium">#</th>
            <th className="py-1 pr-2 font-medium">交通手段</th>
            <th className="py-1 pr-2 font-medium">区間</th>
            <th className="py-1 text-right font-medium">金額</th>
          </tr>
        </thead>
        <tbody>
          {legs.map((leg, i) => (
            <tr key={i} className="border-b border-gray-100">
              <td className="py-1 pr-2 text-gray-400">{startIndex + i + 1}</td>
              <td className="py-1 pr-2">
                <div>{leg.method}</div>
                {leg.fareType && (
                  <div className="text-xs text-gray-500">{leg.fareType}</div>
                )}
                {leg.sourceUrl && (
                  <a
                    href={leg.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-blue-600 underline"
                  >
                    {leg.sourceTitle || "運賃の根拠"}
                  </a>
                )}
              </td>
              <td className="py-1 pr-2 whitespace-nowrap">
                {leg.from} → {leg.to}
              </td>
              <td className="py-1 text-right whitespace-nowrap">
                {formatTransportationCost(leg.cost)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="text-xs font-medium text-gray-600">
            <td colSpan={3} className="pt-1 text-right pr-2">
              小計
            </td>
            <td className="pt-1 text-right whitespace-nowrap">
              {costs.length > 0 ? formatTransportationCost(subtotal) : "—"}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

export function PlanConfirmCard({ data, disabled, onConfirm, onRevise }: Props) {
  const plan = data as Record<string, unknown>;
  const legs = normalizeTransportationLegs(plan.transportation_legs);
  const tripType = (plan.trip_type as string) || "宿泊";
  const { outbound, returnTrip } = splitLegs(legs);

  const handleRevise = () => {
    const feedback = prompt("変更要望を入力してください:");
    if (feedback) onRevise(feedback);
  };

  return (
    <div className={`rounded-xl border bg-white p-5 shadow-sm ${disabled ? "opacity-70" : ""}`}>
      <h3 className="mb-3 text-base font-semibold">📋 旅程プラン確認</h3>

      <div className="mb-3 flex items-center gap-4 text-sm">
        <span>
          <span className="font-medium">種別:</span> {tripType}
        </span>
        {typeof plan.schedule === "string" && (
          <span>
            <span className="font-medium">日程:</span> {plan.schedule}
          </span>
        )}
        {typeof plan.fare_basis === "string" && (
          <span>
            <span className="font-medium">運賃基準:</span>{" "}
            {plan.fare_basis}
          </span>
        )}
      </div>

      {legs.length > 0 && (
        <div className="space-y-3">
          <LegTable label="🛫 往路" legs={outbound} startIndex={0} />
          {returnTrip.length > 0 && (
            <LegTable
              label="🛬 復路"
              legs={returnTrip}
              startIndex={outbound.length}
            />
          )}
        </div>
      )}

      {tripType === "宿泊" && (
        <div className="mt-3">
          <p className="mb-1 text-xs font-semibold text-gray-500">
            🏨 宿泊
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-gray-500">
                <th className="py-1 pr-2 font-medium">宿泊先</th>
                <th className="py-1 pr-2 text-right font-medium">1泊料金</th>
                <th className="py-1 pr-2 text-right font-medium">泊数</th>
                <th className="py-1 text-right font-medium">金額</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-gray-100">
                <td className="py-1 pr-2">
                  {(plan.hotel as string) || "—"}
                </td>
                <td className="py-1 pr-2 text-right whitespace-nowrap">
                  ¥{((plan.hotel_cost_per_night as number) || 0).toLocaleString()}
                </td>
                <td className="py-1 pr-2 text-right">
                  {(plan.hotel_nights as number) || 1}泊
                </td>
                <td className="py-1 text-right whitespace-nowrap">
                  ¥{(
                    ((plan.hotel_cost_per_night as number) || 0) *
                    ((plan.hotel_nights as number) || 1)
                  ).toLocaleString()}
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr className="text-xs font-medium text-gray-600">
                <td colSpan={3} className="pt-1 text-right pr-2">
                  小計
                </td>
                <td className="pt-1 text-right whitespace-nowrap">
                  ¥{(
                    ((plan.hotel_cost_per_night as number) || 0) *
                    ((plan.hotel_nights as number) || 1)
                  ).toLocaleString()}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {/* 💼 日当 */}
      <div className="mt-3">
        <p className="mb-1 text-xs font-semibold text-gray-500">💼 日当</p>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-gray-500">
              <th className="py-1 pr-2 font-medium">種別</th>
              <th className="py-1 pr-2 text-right font-medium">日額</th>
              <th className="py-1 pr-2 text-right font-medium">日数</th>
              <th className="py-1 text-right font-medium">金額</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-gray-100">
              <td className="py-1 pr-2">{tripType === "宿泊" ? "宿泊出張" : "日帰り出張"}</td>
              <td className="py-1 pr-2 text-right whitespace-nowrap">
                ¥{(tripType === "宿泊" ? 5000 : 3000).toLocaleString()}
              </td>
              <td className="py-1 pr-2 text-right">
                {tripType === "宿泊" ? ((plan.hotel_nights as number) || 1) + 1 : 1}日
              </td>
              <td className="py-1 text-right whitespace-nowrap">
                ¥{(tripType === "宿泊"
                  ? 5000 * (((plan.hotel_nights as number) || 1) + 1)
                  : 3000
                ).toLocaleString()}
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr className="text-xs font-medium text-gray-600">
              <td colSpan={3} className="pt-1 text-right pr-2">小計</td>
              <td className="pt-1 text-right whitespace-nowrap">
                ¥{(tripType === "宿泊"
                  ? 5000 * (((plan.hotel_nights as number) || 1) + 1)
                  : 3000
                ).toLocaleString()}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* サマリー */}
      {(() => {
        const transportCost = (plan.transportation_cost as number) || 0;
        const hotelTotal = tripType === "宿泊"
          ? ((plan.hotel_cost_per_night as number) || 0) * ((plan.hotel_nights as number) || 1)
          : 0;
        const dailyAllowance = tripType === "宿泊"
          ? 5000 * (((plan.hotel_nights as number) || 1) + 1)
          : 3000;
        const grandTotal = transportCost + hotelTotal + dailyAllowance;
        return (
          <div className="mt-4 space-y-1 rounded-lg bg-gray-50 px-4 py-3 text-sm">
            <div className="flex justify-between">
              <span className="font-medium">🚄 交通費計</span>
              <span>¥{transportCost.toLocaleString()}</span>
            </div>
            {tripType === "宿泊" && (
              <div className="flex justify-between">
                <span className="font-medium">🏨 宿泊費計</span>
                <span>¥{hotelTotal.toLocaleString()}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="font-medium">💼 日当計</span>
              <span>¥{dailyAllowance.toLocaleString()}</span>
            </div>
            <div className="flex justify-between border-t pt-1 font-semibold">
              <span>💰 合計</span>
              <span>¥{grandTotal.toLocaleString()}</span>
            </div>
          </div>
        );
      })()}

      <div className="mt-4 flex gap-3">
        <button
          onClick={onConfirm}
          disabled={disabled}
          className="rounded-lg bg-green-600 px-5 py-2 text-sm text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {disabled ? "✅ 確定済み" : "✅ このプランで確定"}
        </button>
        {!disabled && (
          <button
            onClick={handleRevise}
            className="rounded-lg border px-5 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            🔄 変更要望
          </button>
        )}
      </div>
    </div>
  );
}
