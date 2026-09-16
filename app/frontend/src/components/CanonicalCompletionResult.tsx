import type {
  CanonicalEvaluationOutput,
  EvaluationStatus,
} from "@/lib/types";

const STATUSES: EvaluationStatus[] = [
  "needs_clarification",
  "draft_ready",
  "policy_blocked",
  "error",
];

function stripJsonFence(value: string): string {
  const trimmed = value.trim();
  const match = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return match?.[1] ?? trimmed;
}

export function parseCanonicalOutput(
  value: string,
): CanonicalEvaluationOutput | null {
  try {
    let parsed: unknown = JSON.parse(stripJsonFence(value));
    if (typeof parsed === "string") {
      parsed = JSON.parse(parsed);
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    const output = parsed as Record<string, unknown>;
    if (
      typeof output.status !== "string" ||
      !STATUSES.includes(output.status as EvaluationStatus)
    ) {
      return null;
    }
    return output as unknown as CanonicalEvaluationOutput;
  } catch {
    return null;
  }
}

function statusLabel(status: EvaluationStatus): string {
  switch (status) {
    case "draft_ready":
      return "申請ドラフト作成済み";
    case "needs_clarification":
      return "追加確認が必要";
    case "policy_blocked":
      return "ポリシーにより停止";
    case "error":
      return "処理エラー";
  }
}

function statusClass(status: EvaluationStatus): string {
  switch (status) {
    case "draft_ready":
      return "is-success";
    case "needs_clarification":
      return "is-warning";
    case "policy_blocked":
    case "error":
      return "is-error";
  }
}

function formatCurrency(value: number | null | undefined): string {
  return typeof value === "number"
    ? `¥${value.toLocaleString("ja-JP")}`
    : "—";
}

export function CanonicalCompletionResult({
  output,
}: {
  output: CanonicalEvaluationOutput;
}) {
  const request = output.request;
  const itinerary = output.itinerary;
  const policy = output.policy;
  const questions = output.clarification_questions ?? [];
  const citations = output.citations ?? [];
  const legs = itinerary?.transportation_legs ?? [];

  return (
    <article className="canonical-result">
      <div className="canonical-result-heading">
        <div>
          <h3>✓ シナリオの処理が完了しました</h3>
          <p>エージェントが返した結果を項目ごとに整理しています。</p>
        </div>
        <span className={`canonical-status ${statusClass(output.status)}`}>
          {statusLabel(output.status)}
        </span>
      </div>

      {questions.length > 0 && (
        <section className="canonical-section">
          <h4>追加確認事項</h4>
          <ul className="canonical-list">
            {questions.map((question, index) => (
              <li key={`${question}-${index}`}>{question}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="canonical-section">
        <h4>リクエスト</h4>
        {request ? (
          <dl className="canonical-detail-grid">
            <div>
              <dt>出発地</dt>
              <dd>{request.departure || "—"}</dd>
            </div>
            <div>
              <dt>目的地</dt>
              <dd>{request.destination || "—"}</dd>
            </div>
            <div>
              <dt>日程</dt>
              <dd>{request.schedule || "—"}</dd>
            </div>
            <div>
              <dt>目的</dt>
              <dd>{request.purpose || "—"}</dd>
            </div>
          </dl>
        ) : (
          <p className="canonical-empty">リクエスト情報はありません。</p>
        )}
      </section>

      <section className="canonical-section">
        <div className="canonical-section-heading">
          <h4>旅程・交通費</h4>
          <strong>
            {formatCurrency(
              output.fare_total ?? itinerary?.transportation_cost,
            )}
          </strong>
        </div>
        {itinerary ? (
          <>
            <dl className="canonical-detail-grid">
              <div>
                <dt>経路</dt>
                <dd>
                  {itinerary.departure || "—"} →{" "}
                  {itinerary.destination || "—"}
                </dd>
              </div>
              <div>
                <dt>旅程種別</dt>
                <dd>{itinerary.trip_type || "—"}</dd>
              </div>
              <div>
                <dt>日程</dt>
                <dd>{itinerary.schedule || "—"}</dd>
              </div>
              <div>
                <dt>旅程合計</dt>
                <dd>{formatCurrency(itinerary.total_cost)}</dd>
              </div>
            </dl>
            {legs.length > 0 ? (
              <ol className="canonical-leg-list">
                {legs.map((leg, index) => (
                  <li
                    key={`${leg.from}-${leg.to}-${leg.method}-${index}`}
                  >
                    <div>
                      <strong>
                        {index + 1}. {leg.method}
                      </strong>
                      <span>{formatCurrency(leg.cost)}</span>
                    </div>
                    <p>
                      {leg.from} → {leg.to}
                    </p>
                    {(leg.direction || leg.fare_type) && (
                      <small>
                        {[leg.direction, leg.fare_type]
                          .filter(Boolean)
                          .join("・")}
                      </small>
                    )}
                    {leg.source_url && (
                      <a
                        href={leg.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {leg.source_title || "運賃情報の出典"}
                      </a>
                    )}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="canonical-empty">交通区間はありません。</p>
            )}
          </>
        ) : (
          <p className="canonical-empty">旅程情報はありません。</p>
        )}
      </section>

      <section className="canonical-section">
        <div className="canonical-section-heading">
          <h4>ポリシー判定</h4>
          <span
            className={`canonical-status ${
              policy?.compliant === true
                ? "is-success"
                : policy?.compliant === false
                  ? "is-error"
                  : ""
            }`}
          >
            {policy?.compliant === true
              ? "準拠"
              : policy?.compliant === false
                ? "非準拠"
                : "判定なし"}
          </span>
        </div>
        {policy ? (
          <>
            {policy.narrative && <p>{policy.narrative}</p>}
            {policy.details.length > 0 && (
              <ul className="canonical-list">
                {policy.details.map((detail, index) => (
                  <li key={`${detail}-${index}`}>{detail}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="canonical-empty">ポリシー判定はありません。</p>
        )}
      </section>

      <section className="canonical-section">
        <h4>申請ドラフト</h4>
        {output.application_draft ? (
          <pre className="canonical-draft">
            {output.application_draft}
          </pre>
        ) : (
          <p className="canonical-empty">申請ドラフトはありません。</p>
        )}
      </section>

      <section className="canonical-section">
        <h4>引用・根拠</h4>
        {citations.length > 0 ? (
          <ul className="canonical-citation-list">
            {citations.map((citation, index) => (
              <li key={`${citation.url}-${index}`}>
                <a href={citation.url} target="_blank" rel="noreferrer">
                  {citation.title || citation.url}
                </a>
                {citation.fare_type && <span>{citation.fare_type}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="canonical-empty">引用・根拠はありません。</p>
        )}
      </section>

      <p className="completion-message">
        再スタートすると、新しい会話で同じリクエストを試せます。
      </p>
    </article>
  );
}
