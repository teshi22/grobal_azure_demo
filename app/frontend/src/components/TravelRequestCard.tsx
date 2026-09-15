"use client";

import {
  formatTransportationCost,
  normalizeTransportationLegs,
} from "@/lib/travel";
import type { TravelRequest } from "@/lib/types";

interface Props {
  request: TravelRequest;
}

export function TravelRequestCard({ request }: Props) {
  const transportationLegs = normalizeTransportationLegs(
    request.transportation_legs,
  );
  const statusColor =
    request.status === "submitted"
      ? "bg-green-100 text-green-800"
      : "bg-gray-100 text-gray-800";

  return (
    <div className="rounded-lg border bg-white p-5 shadow-sm transition hover:shadow-md">
      {/* ヘッダー */}
      <div className="mb-3 flex items-center justify-between">
        <span className="font-mono text-sm font-semibold text-blue-600">
          {request.request_id}
        </span>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${statusColor}`}
        >
          {request.status}
        </span>
      </div>

      {/* ルート */}
      <div className="mb-2 text-lg font-bold">
        📍 {request.departure || "—"} → {request.destination || "—"}
      </div>

      {/* 詳細 */}
      <div className="mb-3 space-y-1 text-sm text-gray-600">
        <p>📅 {request.schedule || "—"}</p>
        <p>🎯 目的: {request.purpose || "—"}</p>
        <p>種別: {request.trip_type || "—"}</p>
      </div>

      {/* 交通手段 */}
      {transportationLegs.length > 0 && (
        <div className="mb-3 rounded bg-gray-50 p-3 text-sm">
          <p className="mb-1 font-medium">🚄 交通手段</p>
          {transportationLegs.map((leg, i) => (
            <p key={i} className="ml-2 text-gray-600">
              {i + 1}. {leg.method} {leg.from} → {leg.to}{" "}
              {formatTransportationCost(leg.cost)}
              {leg.fareType && `（${leg.fareType}）`}
              {leg.sourceUrl && (
                <>
                  {" "}
                  <a
                    href={leg.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-600 underline"
                  >
                    根拠
                  </a>
                </>
              )}
            </p>
          ))}
          <p className="mt-1 font-medium">
            交通費計: ¥{(request.transportation_cost ?? 0).toLocaleString()}
          </p>
        </div>
      )}

      {/* 宿泊 */}
      {request.trip_type === "宿泊" && request.hotel && (
        <div className="mb-3 rounded bg-gray-50 p-3 text-sm">
          <p>
            🏨 {request.hotel} ¥
            {(request.hotel_cost_per_night ?? 0).toLocaleString()}/泊 ×{" "}
            {request.hotel_nights ?? 0}泊
          </p>
        </div>
      )}

      {/* 合計・日時 */}
      <div className="flex items-center justify-between border-t pt-3 text-sm">
        <span className="font-bold text-blue-700">
          💰 合計: ¥{(request.total_cost ?? 0).toLocaleString()}
        </span>
        <span className="text-xs text-gray-400">
          {request.submitted_at
            ? new Date(request.submitted_at).toLocaleString("ja-JP")
            : "—"}
        </span>
      </div>
    </div>
  );
}
