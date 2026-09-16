"use client";

import { useState } from "react";
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

function splitLegs(legs: TransportationLeg[]) {
  const outbound = legs.filter((leg) => leg.direction?.includes("往"));
  const returnTrip = legs.filter((leg) => leg.direction?.includes("復"));
  if (outbound.length + returnTrip.length === legs.length) {
    return { outbound, returnTrip };
  }
  if (legs.length < 2) return { outbound: legs, returnTrip: [] };
  const midpoint = Math.ceil(legs.length / 2);
  return {
    outbound: legs.slice(0, midpoint),
    returnTrip: legs.slice(midpoint),
  };
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
  const subtotal = legs.reduce(
    (sum, leg) => sum + (typeof leg.cost === "number" ? leg.cost : 0),
    0,
  );
  return (
    <div className="hitl-table-section">
      <p className="hitl-section-label">{label}</p>
      <div className="hitl-table-wrap">
        <table className="hitl-table">
          <thead>
            <tr>
              <th>#</th>
              <th>交通手段</th>
              <th>区間</th>
              <th>金額</th>
            </tr>
          </thead>
          <tbody>
            {legs.map((leg, index) => (
              <tr key={`${leg.from}-${leg.to}-${index}`}>
                <td>{startIndex + index + 1}</td>
                <td>
                  {leg.method}
                  {leg.fareType && <small>{leg.fareType}</small>}
                  {leg.sourceUrl && (
                    <a href={leg.sourceUrl} target="_blank" rel="noreferrer">
                      {leg.sourceTitle || "運賃の根拠"}
                    </a>
                  )}
                </td>
                <td>
                  {leg.from} → {leg.to}
                </td>
                <td>{formatTransportationCost(leg.cost)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3}>小計</td>
              <td>{formatTransportationCost(subtotal)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

export function PlanConfirmCard({
  data,
  disabled,
  onConfirm,
  onRevise,
}: Props) {
  const [isRevising, setIsRevising] = useState(false);
  const [feedback, setFeedback] = useState("");
  const plan = data as Record<string, unknown>;
  const legs = normalizeTransportationLegs(plan.transportation_legs);
  const tripType = (plan.trip_type as string) || "宿泊";
  const { outbound, returnTrip } = splitLegs(legs);
  const hotelNights = (plan.hotel_nights as number) || 1;
  const hotelCost = (plan.hotel_cost_per_night as number) || 0;
  const transportationCost = (plan.transportation_cost as number) || 0;
  const hotelTotal = tripType === "宿泊" ? hotelCost * hotelNights : 0;
  const dailyAllowance =
    tripType === "宿泊" ? 5000 * (hotelNights + 1) : 3000;
  const total = transportationCost + hotelTotal + dailyAllowance;

  const submitRevision = (event: React.FormEvent) => {
    event.preventDefault();
    if (!feedback.trim() || disabled) return;
    onRevise(feedback.trim());
    setFeedback("");
    setIsRevising(false);
  };

  return (
    <article className={`hitl-card ${disabled ? "is-disabled" : ""}`}>
      <h3>📋 旅程プラン確認</h3>
      <div className="hitl-meta">
        <span>
          <strong>種別</strong> {tripType}
        </span>
        {typeof plan.schedule === "string" && (
          <span>
            <strong>日程</strong> {plan.schedule}
          </span>
        )}
        {typeof plan.fare_basis === "string" && (
          <span>
            <strong>運賃基準</strong> {plan.fare_basis}
          </span>
        )}
      </div>

      {outbound.length > 0 && (
        <LegTable label="🛫 往路" legs={outbound} startIndex={0} />
      )}
      {returnTrip.length > 0 && (
        <LegTable
          label="🛬 復路"
          legs={returnTrip}
          startIndex={outbound.length}
        />
      )}

      {tripType === "宿泊" && (
        <div className="hitl-summary-line">
          <span>🏨 {(plan.hotel as string) || "宿泊先未定"}・{hotelNights}泊</span>
          <strong>¥{hotelTotal.toLocaleString()}</strong>
        </div>
      )}
      <div className="hitl-cost-summary">
        <div>
          <span>交通費</span>
          <strong>¥{transportationCost.toLocaleString()}</strong>
        </div>
        {tripType === "宿泊" && (
          <div>
            <span>宿泊費</span>
            <strong>¥{hotelTotal.toLocaleString()}</strong>
          </div>
        )}
        <div>
          <span>日当</span>
          <strong>¥{dailyAllowance.toLocaleString()}</strong>
        </div>
        <div className="is-total">
          <span>合計</span>
          <strong>¥{total.toLocaleString()}</strong>
        </div>
      </div>

      {!disabled && isRevising && (
        <form className="hitl-inline-form" onSubmit={submitRevision}>
          <input
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="変更要望を入力..."
            autoFocus
            aria-label="旅程プランの変更要望"
          />
          <button
            type="submit"
            className="chat-primary-button"
            disabled={!feedback.trim()}
          >
            変更要望を送信
          </button>
        </form>
      )}
      <div className="hitl-actions">
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled}
          className="chat-primary-button"
        >
          {disabled ? "✓ 確定済み" : "このプランで確定"}
        </button>
        {!disabled && (
          <button
            type="button"
            onClick={() => setIsRevising((value) => !value)}
            className="chat-secondary-button"
          >
            変更要望
          </button>
        )}
      </div>
    </article>
  );
}
