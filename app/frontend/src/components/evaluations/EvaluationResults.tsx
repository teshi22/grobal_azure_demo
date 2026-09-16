"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  CanonicalEvaluationOutput,
  EvaluationCaseComparison,
  EvaluationResult,
  EvaluationRun,
  EvaluationScenario,
  HumanReview,
  HumanWinner,
} from "@/lib/types";

interface EvaluationResultsProps {
  run: EvaluationRun | null;
  comparisons: EvaluationCaseComparison[];
  isLoading: boolean;
  error: string | null;
  onReload: () => Promise<void>;
  onSaveReview: (caseId: string, review: HumanReview) => Promise<void>;
}

interface ScenarioMeta {
  id: EvaluationScenario;
  shortLabel: string;
  label: string;
}

const SCENARIOS: ScenarioMeta[] = [
  {
    id: "agent_framework_workflow",
    shortLabel: "シナリオ A",
    label: "Agent Framework ワークフロー",
  },
  {
    id: "single_prompt_agent",
    shortLabel: "シナリオ B",
    label: "単一プロンプト エージェント",
  },
];

const ACTIVE_STATUSES = new Set(["queued", "pending", "running", "in_progress"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function firstNumber(
  record: Record<string, unknown> | undefined,
  keys: string[],
): number | null {
  if (!record) return null;
  for (const key of keys) {
    const value = finiteNumber(record[key]);
    if (value !== null) return value;
  }
  return null;
}

function aggregateMetric(
  run: EvaluationRun,
  scenario: EvaluationScenario,
  keys: string[],
): number | null {
  const aggregate = run.aggregate;
  const scenarioRecord = isRecord(aggregate[scenario])
    ? aggregate[scenario]
    : undefined;
  const scenarios = isRecord(aggregate.scenarios)
    ? aggregate.scenarios
    : undefined;
  const nestedScenario =
    scenarios && isRecord(scenarios[scenario])
      ? scenarios[scenario]
      : undefined;
  const direct = firstNumber(scenarioRecord, keys);
  if (direct !== null) return direct;
  const nested = firstNumber(nestedScenario, keys);
  if (nested !== null) return nested;

  for (const key of keys) {
    const metric = aggregate[key];
    if (isRecord(metric)) {
      const value = finiteNumber(metric[scenario]);
      if (value !== null) return value;
    }
    const prefixed = finiteNumber(aggregate[`${scenario}_${key}`]);
    if (prefixed !== null) return prefixed;
  }
  return null;
}

function scenarioAggregate(
  run: EvaluationRun,
  scenario: EvaluationScenario,
): Record<string, unknown> | undefined {
  const scenarios = isRecord(run.aggregate.scenarios)
    ? run.aggregate.scenarios
    : undefined;
  return scenarios && isRecord(scenarios[scenario])
    ? scenarios[scenario]
    : isRecord(run.aggregate[scenario])
      ? run.aggregate[scenario]
      : undefined;
}

function average(values: Array<number | null | undefined>): number | null {
  const numbers = values.filter(
    (value): value is number =>
      typeof value === "number" && Number.isFinite(value),
  );
  if (numbers.length === 0) return null;
  return numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
}

function resultFor(
  comparison: EvaluationCaseComparison,
  scenario: EvaluationScenario,
): EvaluationResult | null {
  return comparison[scenario];
}

function resultMetric(
  comparisons: EvaluationCaseComparison[],
  scenario: EvaluationScenario,
  key: "overall_score" | "rubric_score" | "deterministic_score",
): number | null {
  return average(
    comparisons.map((comparison) => resultFor(comparison, scenario)?.[key]),
  );
}

function totalTokens(value: Record<string, unknown> | undefined): number | null {
  if (!value) return null;
  const total = firstNumber(value, [
    "total_tokens",
    "total",
    "tokens",
    "token_count",
  ]);
  if (total !== null) return total;
  const input = firstNumber(value, ["input_tokens", "prompt_tokens"]);
  const output = firstNumber(value, ["output_tokens", "completion_tokens"]);
  if (input !== null || output !== null) return (input ?? 0) + (output ?? 0);
  return sumAvailable(
    Object.values(value)
      .filter(isRecord)
      .map((nested) => {
        const nestedTotal = firstNumber(nested, [
          "total_tokens",
          "total",
          "tokens",
          "token_count",
        ]);
        if (nestedTotal !== null) return nestedTotal;
        const nestedInput = firstNumber(nested, [
          "input_tokens",
          "prompt_tokens",
        ]);
        const nestedOutput = firstNumber(nested, [
          "output_tokens",
          "completion_tokens",
        ]);
        return nestedInput !== null || nestedOutput !== null
          ? (nestedInput ?? 0) + (nestedOutput ?? 0)
          : null;
      }),
  );
}

function estimatedCost(
  value: Record<string, unknown> | undefined,
): number | null {
  return firstNumber(value, [
    "total_usd",
    "estimated_cost_usd",
    "cost_usd",
    "usd",
    "amount",
    "total",
  ]);
}

function sumAvailable(values: Array<number | null>): number | null {
  const available = values.filter((value): value is number => value !== null);
  return available.length > 0
    ? available.reduce((sum, value) => sum + value, 0)
    : null;
}

function scenarioTokens(
  run: EvaluationRun,
  comparisons: EvaluationCaseComparison[],
  scenario: EvaluationScenario,
): number | null {
  const fromResults = sumAvailable(
    comparisons.map((comparison) =>
      totalTokens(resultFor(comparison, scenario)?.token_usage),
    ),
  );
  if (fromResults !== null) return fromResults;
  const scenarioRun = run.scenario_runs[scenario];
  const aggregate = scenarioAggregate(run, scenario);
  return (
    totalTokens(scenarioRun?.token_usage) ??
    totalTokens(
      aggregate && isRecord(aggregate.per_model_usage)
        ? aggregate.per_model_usage
        : undefined,
    ) ??
    aggregateMetric(run, scenario, ["total_tokens", "tokens"])
  );
}

function scenarioCost(
  run: EvaluationRun,
  comparisons: EvaluationCaseComparison[],
  scenario: EvaluationScenario,
): number | null {
  const aggregate = scenarioAggregate(run, scenario);
  const aggregateCost =
    aggregate && isRecord(aggregate.estimated_cost)
      ? aggregate.estimated_cost
      : undefined;
  if (aggregateCost?.available === false) return null;
  const aggregateTotal = estimatedCost(aggregateCost);
  if (aggregateTotal !== null) return aggregateTotal;

  const resultCosts = comparisons.map((comparison) =>
    estimatedCost(resultFor(comparison, scenario)?.estimated_cost),
  );
  if (
    resultCosts.length > 0 &&
    resultCosts.every((value): value is number => value !== null)
  ) {
    return resultCosts.reduce((sum, value) => sum + value, 0);
  }
  const scenarioRun = run.scenario_runs[scenario];
  return (
    estimatedCost(scenarioRun?.estimated_cost) ??
    aggregateMetric(run, scenario, [
      "estimated_cost_usd",
      "total_cost_usd",
      "cost_usd",
    ])
  );
}

function formatScore(value: number | null): string {
  return value === null ? "—" : value.toFixed(1);
}

function formatDuration(value: number | null): string {
  if (value === null) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${(seconds % 60).toFixed(0)} 秒`;
}

function formatTokens(value: number | null): string {
  return value === null ? "—" : Math.round(value).toLocaleString("ja-JP");
}

function formatCost(value: number | null): string {
  return value === null ? "利用不可" : `$${value.toFixed(4)}`;
}

function formatPricingDate(value: string): string {
  if (!value) return "価格情報の更新日なし";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? `価格更新: ${value}`
    : `価格更新: ${date.toLocaleDateString("ja-JP")}`;
}

function outputOf(result: EvaluationResult): CanonicalEvaluationOutput {
  return result.output as CanonicalEvaluationOutput;
}

function statusLabel(status: string | undefined): string {
  switch (status) {
    case "draft_ready":
      return "ドラフト作成";
    case "needs_clarification":
      return "追加確認";
    case "policy_blocked":
      return "ポリシー停止";
    case "error":
      return "エラー";
    default:
      return status || "不明";
  }
}

function policyLabel(value: boolean | null | undefined): string {
  if (value === true) return "準拠";
  if (value === false) return "非準拠";
  return "判定なし";
}

function displayEvaluatorValue(value: string | number | null): string {
  if (value === null) return "—";
  if (typeof value === "number") return Number.isInteger(value)
    ? value.toString()
    : value.toFixed(2);
  return value;
}

function ScenarioResultPanel({
  scenario,
  result,
  status,
  error,
}: {
  scenario: ScenarioMeta;
  result: EvaluationResult | null;
  status?: string;
  error?: string;
}) {
  if (!result) {
    return (
      <section className="eval-result-panel is-missing">
        <div className="eval-result-panel-heading">
          <div>
            <span>{scenario.shortLabel}</span>
            <h4>{scenario.label}</h4>
          </div>
          {status && <span className="eval-status">{status}</span>}
        </div>
        {error && <div className="eval-alert eval-alert-error">{error}</div>}
        <div className="eval-empty">
          このシナリオの結果はありません。部分完了または失敗の可能性があります。
        </div>
      </section>
    );
  }

  const output = outputOf(result);
  const policy = output.policy;
  const itinerary = output.itinerary;
  const citations = output.citations ?? [];
  const evaluatorEntries = Object.entries(result.evaluator_scores ?? {});

  return (
    <section className="eval-result-panel">
      <div className="eval-result-panel-heading">
        <div>
          <span>{scenario.shortLabel}</span>
          <h4>{scenario.label}</h4>
        </div>
        <span className="eval-status">{statusLabel(output.status)}</span>
      </div>
      <div className="eval-result-score-strip">
        <div>
          <span>総合</span>
          <strong>{formatScore(result.overall_score)}</strong>
        </div>
        <div>
          <span>Foundry</span>
          <strong>{formatScore(result.rubric_score)}</strong>
        </div>
        <div>
          <span>決定的</span>
          <strong>{formatScore(result.deterministic_score)}</strong>
        </div>
      </div>

      {result.error && (
        <div className="eval-alert eval-alert-error">{result.error}</div>
      )}

      <div className="eval-output-group">
        <h5>申請ドラフト</h5>
        {output.application_draft ? (
          <pre className="eval-draft">{output.application_draft}</pre>
        ) : (
          <p className="eval-muted">ドラフトはありません。</p>
        )}
        {(output.clarification_questions?.length ?? 0) > 0 && (
          <div className="eval-subpanel">
            <strong>追加確認事項</strong>
            <ul>
              {output.clarification_questions?.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="eval-output-group">
        <div className="eval-output-heading">
          <h5>ポリシー判定</h5>
          <span
            className={`eval-status ${
              policy?.compliant === true
                ? "is-success"
                : policy?.compliant === false
                  ? "is-error"
                  : ""
            }`}
          >
            {policyLabel(policy?.compliant)}
          </span>
        </div>
        {policy?.narrative && <p>{policy.narrative}</p>}
        {(policy?.details.length ?? 0) > 0 && (
          <ul>
            {policy?.details.map((detail) => <li key={detail}>{detail}</li>)}
          </ul>
        )}
      </div>

      <div className="eval-output-group">
        <h5>旅程・運賃</h5>
        {itinerary ? (
          <>
            <dl className="eval-itinerary">
              <div>
                <dt>経路</dt>
                <dd>
                  {itinerary.departure} → {itinerary.destination}
                </dd>
              </div>
              <div>
                <dt>日程</dt>
                <dd>{itinerary.schedule || "—"}</dd>
              </div>
              <div>
                <dt>交通費</dt>
                <dd>
                  ¥{itinerary.transportation_cost.toLocaleString("ja-JP")}
                </dd>
              </div>
              <div>
                <dt>合計</dt>
                <dd>¥{itinerary.total_cost.toLocaleString("ja-JP")}</dd>
              </div>
            </dl>
            <ol className="eval-leg-list">
              {itinerary.transportation_legs.map((leg, index) => (
                <li key={`${leg.from}-${leg.to}-${index}`}>
                  <strong>
                    {leg.method}: {leg.from} → {leg.to}
                  </strong>
                  <span>
                    ¥{leg.cost.toLocaleString("ja-JP")}
                    {leg.fare_type ? `・${leg.fare_type}` : ""}
                  </span>
                  {leg.source_url && (
                    <a
                      href={leg.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {leg.source_title || "運賃根拠"}
                    </a>
                  )}
                </li>
              ))}
            </ol>
          </>
        ) : (
          <p className="eval-muted">旅程情報はありません。</p>
        )}
        {citations.length > 0 && (
          <div className="eval-citations">
            <strong>引用・根拠</strong>
            <ul>
              {citations.map((citation, index) => (
                <li key={`${citation.url}-${index}`}>
                  <a href={citation.url} target="_blank" rel="noreferrer">
                    {citation.title || citation.url}
                  </a>
                  {citation.fare_type && <span>{citation.fare_type}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="eval-output-group">
        <h5>自動評価の理由</h5>
        {result.deterministic_checks.length > 0 && (
          <ul className="eval-check-list">
            {result.deterministic_checks.map((check) => (
              <li key={check.name}>
                <span
                  className={`eval-check-mark ${
                    check.passed ? "is-passed" : "is-failed"
                  }`}
                >
                  {check.passed ? "✓" : "×"}
                </span>
                <div>
                  <strong>
                    {check.name}（{check.score.toFixed(1)}）
                  </strong>
                  {check.detail && <p>{check.detail}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}
        {evaluatorEntries.length > 0 && (
          <dl className="eval-evaluator-values">
            {evaluatorEntries.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{displayEvaluatorValue(value)}</dd>
              </div>
            ))}
          </dl>
        )}
        {result.deterministic_checks.length === 0 &&
          evaluatorEntries.length === 0 && (
            <p className="eval-muted">評価理由はありません。</p>
          )}
      </div>
    </section>
  );
}

function HumanReviewForm({
  comparison,
  onSave,
}: {
  comparison: EvaluationCaseComparison;
  onSave: (caseId: string, review: HumanReview) => Promise<void>;
}) {
  const existing = comparison.human_review;
  const [agentFrameworkScore, setAgentFrameworkScore] = useState(
    existing?.agent_framework_score?.toString() ?? "",
  );
  const [singleAgentScore, setSingleAgentScore] = useState(
    existing?.single_agent_score?.toString() ?? "",
  );
  const [winner, setWinner] = useState<HumanWinner | "">(
    existing?.winner ?? "",
  );
  const [comment, setComment] = useState(existing?.comment ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setAgentFrameworkScore(
      comparison.human_review?.agent_framework_score?.toString() ?? "",
    );
    setSingleAgentScore(
      comparison.human_review?.single_agent_score?.toString() ?? "",
    );
    setWinner(comparison.human_review?.winner ?? "");
    setComment(comparison.human_review?.comment ?? "");
    setError(null);
  }, [comparison.human_review]);

  const save = async () => {
    const scoreA = Number(agentFrameworkScore);
    const scoreB = Number(singleAgentScore);
    if (
      !Number.isInteger(scoreA) ||
      scoreA < 1 ||
      scoreA > 5 ||
      !Number.isInteger(scoreB) ||
      scoreB < 1 ||
      scoreB > 5
    ) {
      setError("両シナリオのスコアを 1〜5 で選択してください。");
      return;
    }
    if (!winner) {
      setError("勝者または引き分けを選択してください。");
      return;
    }
    if (comment.length > 4000) {
      setError("コメントは 4,000 文字以内で入力してください。");
      return;
    }
    setIsSaving(true);
    setSaved(false);
    setError(null);
    try {
      await onSave(comparison.case.id, {
        agent_framework_score: scoreA,
        single_agent_score: scoreB,
        winner,
        comment,
      });
      setSaved(true);
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "レビューを保存できませんでした。",
      );
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="eval-human-review" aria-label="人手評価">
      <div className="eval-human-review-heading">
        <div>
          <p className="eval-eyebrow">人手評価</p>
          <h4>比較レビュー</h4>
        </div>
        {existing && <span className="eval-status is-success">レビュー済み</span>}
      </div>
      <div className="eval-human-score-grid">
        <label className="eval-field">
          <span>シナリオ A のスコア</span>
          <select
            value={agentFrameworkScore}
            onChange={(event) => {
              setAgentFrameworkScore(event.target.value);
              setSaved(false);
            }}
          >
            <option value="">選択</option>
            {[1, 2, 3, 4, 5].map((score) => (
              <option key={score} value={score}>
                {score} / 5
              </option>
            ))}
          </select>
        </label>
        <label className="eval-field">
          <span>シナリオ B のスコア</span>
          <select
            value={singleAgentScore}
            onChange={(event) => {
              setSingleAgentScore(event.target.value);
              setSaved(false);
            }}
          >
            <option value="">選択</option>
            {[1, 2, 3, 4, 5].map((score) => (
              <option key={score} value={score}>
                {score} / 5
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="eval-winner-field">
        <legend>勝者</legend>
        <label>
          <input
            type="radio"
            name={`winner-${comparison.case.id}`}
            checked={winner === "agent_framework_workflow"}
            onChange={() => {
              setWinner("agent_framework_workflow");
              setSaved(false);
            }}
          />
          シナリオ A
        </label>
        <label>
          <input
            type="radio"
            name={`winner-${comparison.case.id}`}
            checked={winner === "single_prompt_agent"}
            onChange={() => {
              setWinner("single_prompt_agent");
              setSaved(false);
            }}
          />
          シナリオ B
        </label>
        <label>
          <input
            type="radio"
            name={`winner-${comparison.case.id}`}
            checked={winner === "tie"}
            onChange={() => {
              setWinner("tie");
              setSaved(false);
            }}
          />
          引き分け
        </label>
      </fieldset>
      <label className="eval-field">
        <span>コメント</span>
        <textarea
          rows={3}
          maxLength={4000}
          value={comment}
          onChange={(event) => {
            setComment(event.target.value);
            setSaved(false);
          }}
          placeholder="判断理由や気づきを記録"
        />
        <small>{comment.length.toLocaleString("ja-JP")} / 4,000</small>
      </label>
      {error && <div className="eval-alert eval-alert-error">{error}</div>}
      <div className="eval-review-actions">
        <span className="eval-success-text" aria-live="polite">
          {saved ? "保存しました" : ""}
        </span>
        <button
          className="eval-button eval-button-primary"
          onClick={() => void save()}
          disabled={isSaving}
        >
          {isSaving ? "保存中…" : "レビューを保存"}
        </button>
      </div>
    </section>
  );
}

export function EvaluationResults({
  run,
  comparisons,
  isLoading,
  error,
  onReload,
  onSaveReview,
}: EvaluationResultsProps) {
  const humanSummary = useMemo(() => {
    const reviews = comparisons
      .map((comparison) => comparison.human_review)
      .filter((review): review is HumanReview => Boolean(review));
    return {
      reviewed: reviews.length,
      averageA: average(reviews.map((review) => review.agent_framework_score)),
      averageB: average(reviews.map((review) => review.single_agent_score)),
      winsA: reviews.filter(
        (review) => review.winner === "agent_framework_workflow",
      ).length,
      winsB: reviews.filter(
        (review) => review.winner === "single_prompt_agent",
      ).length,
      ties: reviews.filter((review) => review.winner === "tie").length,
    };
  }, [comparisons]);

  if (!run) {
    return (
      <section aria-labelledby="results-heading" className="eval-section">
        <div className="eval-section-heading">
          <div>
            <p className="eval-eyebrow">並列比較</p>
            <h2 id="results-heading">比較結果</h2>
            <p>評価ランを選択すると結果を表示します。</p>
          </div>
        </div>
        <div className="eval-empty">
          評価ランタブから表示するランを選択してください。
        </div>
      </section>
    );
  }

  const metrics = SCENARIOS.map((scenario) => {
    const scenarioRun = run.scenario_runs[scenario.id];
    return {
      ...scenario,
      overall:
        resultMetric(comparisons, scenario.id, "overall_score") ??
        aggregateMetric(run, scenario.id, ["overall_score", "score"]),
      rubric:
        resultMetric(comparisons, scenario.id, "rubric_score") ??
        aggregateMetric(run, scenario.id, [
          "rubric_score",
          "foundry_rubric_score",
        ]),
      deterministic:
        resultMetric(comparisons, scenario.id, "deterministic_score") ??
        aggregateMetric(run, scenario.id, ["deterministic_score"]),
      duration:
        finiteNumber(scenarioRun?.duration_ms) ??
        aggregateMetric(run, scenario.id, [
          "batch_duration_ms",
          "duration_ms",
        ]),
      tokens: scenarioTokens(run, comparisons, scenario.id),
      cost: scenarioCost(run, comparisons, scenario.id),
    };
  });
  const runIsActive = ACTIVE_STATUSES.has(run.status.toLowerCase());
  const anyCostAvailable = metrics.some((metric) => metric.cost !== null);

  return (
    <section aria-labelledby="results-heading" className="eval-section">
      <div className="eval-section-heading">
        <div>
          <p className="eval-eyebrow">並列比較</p>
          <h2 id="results-heading">比較結果</h2>
          <p>
            {run.name || run.id} ・ {comparisons.length} ケース
          </p>
        </div>
        <button
          className="eval-button eval-button-secondary"
          onClick={onReload}
        >
          更新
        </button>
      </div>

      {runIsActive && (
        <div className="eval-alert eval-alert-info" aria-live="polite">
          評価を実行中です。完了まで結果を自動更新します。
        </div>
      )}
      {error && <div className="eval-alert eval-alert-error">{error}</div>}

      <section aria-labelledby="automatic-summary-heading">
        <div className="eval-group-heading">
          <div>
            <p className="eval-eyebrow">自動評価</p>
            <h3 id="automatic-summary-heading">集計サマリー</h3>
          </div>
          <span>{formatPricingDate(run.pricing_updated_at)}</span>
        </div>
        <div className="eval-summary-grid">
          {[
            {
              label: "総合スコア",
              values: metrics.map((metric) => formatScore(metric.overall)),
            },
            {
              label: "Foundry ルーブリック",
              values: metrics.map((metric) => formatScore(metric.rubric)),
            },
            {
              label: "決定的スコア",
              values: metrics.map((metric) =>
                formatScore(metric.deterministic),
              ),
            },
            {
              label: "バッチ時間",
              values: metrics.map((metric) => formatDuration(metric.duration)),
            },
            {
              label: "トークン",
              values: metrics.map((metric) => formatTokens(metric.tokens)),
            },
            {
              label: "推定コスト",
              values: metrics.map((metric) => formatCost(metric.cost)),
              note: anyCostAvailable
                ? formatPricingDate(run.pricing_updated_at)
                : "料金表または使用量がないため利用不可",
            },
          ].map((card) => (
            <article className="eval-summary-card" key={card.label}>
              <span>{card.label}</span>
              {metrics.map((metric, index) => (
                <div key={metric.id}>
                  <small>{metric.shortLabel}</small>
                  <strong>{card.values[index]}</strong>
                </div>
              ))}
              {card.note && <p>{card.note}</p>}
            </article>
          ))}
        </div>
      </section>

      <section
        className="eval-human-summary"
        aria-labelledby="human-summary-heading"
      >
        <div className="eval-group-heading">
          <div>
            <p className="eval-eyebrow">人手評価</p>
            <h3 id="human-summary-heading">レビュー集計</h3>
          </div>
          <span>
            {humanSummary.reviewed} / {comparisons.length} 件レビュー済み
          </span>
        </div>
        <div className="eval-human-summary-grid">
          <div>
            <span>平均スコア A</span>
            <strong>
              {humanSummary.averageA === null
                ? "—"
                : `${humanSummary.averageA.toFixed(2)} / 5`}
            </strong>
          </div>
          <div>
            <span>平均スコア B</span>
            <strong>
              {humanSummary.averageB === null
                ? "—"
                : `${humanSummary.averageB.toFixed(2)} / 5`}
            </strong>
          </div>
          <div>
            <span>A の勝利</span>
            <strong>{humanSummary.winsA}</strong>
          </div>
          <div>
            <span>B の勝利</span>
            <strong>{humanSummary.winsB}</strong>
          </div>
          <div>
            <span>引き分け</span>
            <strong>{humanSummary.ties}</strong>
          </div>
        </div>
      </section>

      {isLoading ? (
        <div className="eval-empty" aria-live="polite">
          結果を読み込み中…
        </div>
      ) : comparisons.length === 0 ? (
        <div className="eval-empty">
          まだ比較結果がありません。実行中の場合は完了後に自動表示されます。
        </div>
      ) : (
        <div className="eval-comparison-list">
          {comparisons.map((comparison, index) => (
            <article
              className="eval-comparison-card"
              key={comparison.case.id}
            >
              <div className="eval-case-heading">
                <div>
                  <span>CASE {String(index + 1).padStart(2, "0")}</span>
                  <h3>{comparison.case.title}</h3>
                  <code>{comparison.case.id}</code>
                </div>
                <div className="eval-tags">
                  {comparison.case.tags.map((tag) => (
                    <span key={tag}>{tag}</span>
                  ))}
                </div>
              </div>
              <blockquote>{comparison.case.input}</blockquote>
              <div className="eval-side-by-side">
                {SCENARIOS.map((scenario) => (
                  <ScenarioResultPanel
                    key={scenario.id}
                    scenario={scenario}
                    result={resultFor(comparison, scenario.id)}
                    status={comparison.scenario_statuses?.[scenario.id]}
                    error={comparison.scenario_errors?.[scenario.id]}
                  />
                ))}
              </div>
              <HumanReviewForm
                comparison={comparison}
                onSave={onSaveReview}
              />
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
