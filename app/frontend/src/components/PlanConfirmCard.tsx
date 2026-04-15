"use client";

interface Props {
  data: Record<string, unknown>;
  onConfirm: () => void;
  onRevise: (feedback: string) => void;
}

interface TransportLeg {
  method: string;
  from: string;
  to: string;
  cost: number;
}

export function PlanConfirmCard({ data, onConfirm, onRevise }: Props) {
  const plan = data as Record<string, unknown>;
  const legs = (plan.transportation_legs as TransportLeg[]) || [];
  const tripType = (plan.trip_type as string) || "宿泊";

  const handleRevise = () => {
    const feedback = prompt("変更要望を入力してください:");
    if (feedback) onRevise(feedback);
  };

  return (
    <div className="rounded-xl border bg-white p-5 shadow-sm">
      <h3 className="mb-3 text-base font-semibold">📋 旅程プラン確認</h3>

      <div className="space-y-2 text-sm">
        <p>
          <span className="font-medium">種別:</span> {tripType}
        </p>

        {legs.length > 0 && (
          <div>
            <span className="font-medium">🚄 交通手段:</span>
            <ul className="ml-4 mt-1 list-disc">
              {legs.map((leg, i) => (
                <li key={i}>
                  {leg.method} — {leg.from} → {leg.to} ¥
                  {leg.cost?.toLocaleString()}
                </li>
              ))}
            </ul>
          </div>
        )}

        <p>
          <span className="font-medium">💴 交通費計:</span> ¥
          {((plan.transportation_cost as number) || 0).toLocaleString()}
        </p>

        {tripType === "宿泊" && (
          <p>
            <span className="font-medium">🏨 宿泊:</span>{" "}
            {(plan.hotel as string) || "—"} ¥
            {((plan.hotel_cost_per_night as number) || 0).toLocaleString()}
            /泊 × {(plan.hotel_nights as number) || 1}泊
          </p>
        )}

        <p>
          <span className="font-medium">💰 合計:</span> ¥
          {((plan.total_cost as number) || 0).toLocaleString()}
        </p>
      </div>

      <div className="mt-4 flex gap-3">
        <button
          onClick={onConfirm}
          className="rounded-lg bg-green-600 px-5 py-2 text-sm text-white hover:bg-green-700"
        >
          ✅ このプランで確定
        </button>
        <button
          onClick={handleRevise}
          className="rounded-lg border px-5 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          🔄 変更要望
        </button>
      </div>
    </div>
  );
}
