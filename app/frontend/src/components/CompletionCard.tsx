"use client";

import {
  formatTransportationCost,
  normalizeTransportationLegs,
} from "@/lib/travel";
import type { CompletionData } from "@/lib/types";
import {
  CanonicalCompletionResult,
  parseCanonicalOutput,
} from "./CanonicalCompletionResult";

interface Props {
  data: CompletionData;
  isPlayground?: boolean;
}

export function CompletionCard({ data, isPlayground = false }: Props) {
  if (isPlayground) {
    const canonicalOutput = parseCanonicalOutput(data.output);
    if (canonicalOutput) {
      return <CanonicalCompletionResult output={canonicalOutput} />;
    }
    return (
      <article className="chat-completion-card">
        <div className="chat-card-heading">
          <h3>✓ 対話が完了しました</h3>
        </div>
        <p className="canonical-empty">
          構造化された結果を表示できませんでした。再スタートしてもう一度お試しください。
        </p>
        <p className="completion-message">
          完了データは受信しましたが、JSON の内容を画面表示用に解析できませんでした。
        </p>
      </article>
    );
  }

  const wasCancelled =
    !data.requestId &&
    !data.plan &&
    data.output.includes("キャンセル");
  if (wasCancelled) {
    return (
      <article className="chat-completion-card">
        <div className="chat-card-heading">
          <h3>申請送信をキャンセルしました</h3>
        </div>
        <p className="completion-message">{data.output}</p>
      </article>
    );
  }

  const plan = data.plan || {};
  const legs = normalizeTransportationLegs(plan.transportation_legs);
  const tripType = (plan.trip_type as string) || "日帰り";
  const destination = plan.destination as string | undefined;
  const schedule = plan.schedule as string | undefined;
  const purpose = plan.purpose as string | undefined;
  const hotel = plan.hotel as string | undefined;
  const transportationCost = (plan.transportation_cost as number) || 0;
  const hotelCost = (plan.hotel_cost_per_night as number) || 0;
  const hotelNights = (plan.hotel_nights as number) || 0;
  const hotelTotal = hotelCost * hotelNights;
  const dailyAllowance =
    tripType === "宿泊" ? 5000 * (hotelNights + 1) : 3000;
  const total = transportationCost + hotelTotal + dailyAllowance;
  const hasPlan = legs.length > 0 || Boolean(destination);

  return (
    <article className="chat-completion-card">
      <div className="chat-card-heading">
        <h3>✓ 出張申請完了</h3>
        {data.requestId && <span>申請番号: {data.requestId}</span>}
      </div>

      {hasPlan ? (
        <>
          <dl className="hitl-detail-grid">
            {destination && (
              <div>
                <dt>出張先</dt>
                <dd>{destination}</dd>
              </div>
            )}
            {schedule && (
              <div>
                <dt>日程</dt>
                <dd>{schedule}</dd>
              </div>
            )}
            {purpose && (
              <div>
                <dt>目的</dt>
                <dd>{purpose}</dd>
              </div>
            )}
            <div>
              <dt>種別</dt>
              <dd>{tripType}</dd>
            </div>
          </dl>

          {legs.length > 0 && (
            <div className="completion-section">
              <strong>🚄 交通手段</strong>
              {legs.map((leg, index) => (
                <div className="completion-leg" key={`${leg.from}-${index}`}>
                  <span>
                    {index + 1}. {leg.method} {leg.from} → {leg.to}
                  </span>
                  <strong>{formatTransportationCost(leg.cost)}</strong>
                </div>
              ))}
            </div>
          )}

          {tripType === "宿泊" && hotel && (
            <div className="hitl-summary-line">
              <span>🏨 {hotel}・{hotelNights}泊</span>
              <strong>¥{hotelTotal.toLocaleString()}</strong>
            </div>
          )}

          {data.policyDisplay && (
            <div className="hitl-policy-box">
              <strong>📋 旅費規程チェック: OK</strong>
              {data.policyDisplay.split("\n").map((line, index) => (
                <p key={`${line}-${index}`}>{line}</p>
              ))}
            </div>
          )}

          <div className="hitl-cost-summary">
            <div>
              <span>交通費</span>
              <strong>¥{transportationCost.toLocaleString()}</strong>
            </div>
            {hotelTotal > 0 && (
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
        </>
      ) : (
        <pre className="completion-output">{data.output}</pre>
      )}

      <p className="completion-message">
        {`出張申請書を申請システムへ送信しました${
          data.requestId ? `（申請番号: ${data.requestId}）` : ""
        }。`}
      </p>
    </article>
  );
}
