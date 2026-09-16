"use client";

import { useCallback, useState } from "react";
import { runScenario } from "@/lib/api";
import type {
  CanonicalEvaluationOutput,
  EvaluationScenario,
  ScenarioRunResponse,
} from "@/lib/types";

interface ScenarioDefinition {
  id: EvaluationScenario;
  eyebrow: string;
  title: string;
  description: string;
}

interface ScenarioState {
  result: ScenarioRunResponse | null;
  error: string | null;
  isRunning: boolean;
}

const SCENARIOS: ScenarioDefinition[] = [
  {
    id: "agent_framework_workflow",
    eyebrow: "Scenario A",
    title: "Agent Framework ワークフロー",
    description:
      "複数ステップのワークフローとして、依頼の整理・旅程・ポリシー判定・申請ドラフトを生成します。",
  },
  {
    id: "single_prompt_agent",
    eyebrow: "Scenario B",
    title: "単一プロンプト エージェント",
    description:
      "同じ依頼を単一のエージェントで処理し、正規化された出力を一度に生成します。",
  },
];

const INITIAL_STATE: Record<EvaluationScenario, ScenarioState> = {
  agent_framework_workflow: {
    result: null,
    error: null,
    isRunning: false,
  },
  single_prompt_agent: {
    result: null,
    error: null,
    isRunning: false,
  },
};

function errorMessage(value: unknown): string {
  return value instanceof Error
    ? value.message
    : "シナリオを実行できませんでした。時間をおいて再度お試しください。";
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function firstNumber(
  value: Record<string, unknown>,
  keys: string[],
): number | null {
  for (const key of keys) {
    const number = finiteNumber(value[key]);
    if (number !== null) return number;
  }
  return null;
}

function totalTokens(value: Record<string, unknown>): number | null {
  const direct = firstNumber(value, [
    "total_tokens",
    "total",
    "tokens",
    "token_count",
  ]);
  if (direct !== null) return direct;

  const input = firstNumber(value, ["input_tokens", "prompt_tokens"]);
  const output = firstNumber(value, ["output_tokens", "completion_tokens"]);
  if (input !== null || output !== null) return (input ?? 0) + (output ?? 0);

  const nestedTotals = Object.values(value)
    .filter(
      (item): item is Record<string, unknown> =>
        Boolean(item) && typeof item === "object" && !Array.isArray(item),
    )
    .map(totalTokens)
    .filter((item): item is number => item !== null);
  return nestedTotals.length > 0
    ? nestedTotals.reduce((sum, item) => sum + item, 0)
    : null;
}

function formatDuration(value: number): string {
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(1)} 秒`;
}

function formatCurrency(value: number | null | undefined): string {
  return typeof value === "number"
    ? `¥${value.toLocaleString("ja-JP")}`
    : "—";
}

function statusLabel(status: string): string {
  switch (status) {
    case "draft_ready":
      return "申請ドラフト作成済み";
    case "needs_clarification":
      return "追加確認が必要";
    case "policy_blocked":
      return "ポリシーにより停止";
    case "error":
      return "エラー";
    default:
      return status || "不明";
  }
}

function statusClass(status: string): string {
  switch (status) {
    case "draft_ready":
      return "is-success";
    case "needs_clarification":
      return "is-warning";
    case "policy_blocked":
    case "error":
      return "is-error";
    default:
      return "";
  }
}

function EmptyValue({ children }: { children: string }) {
  return <p className="play-empty-value">{children}</p>;
}

function CanonicalOutput({ result }: { result: ScenarioRunResponse }) {
  const output: CanonicalEvaluationOutput = result.output;
  const request = output.request;
  const itinerary = output.itinerary;
  const policy = output.policy;
  const questions = output.clarification_questions ?? [];
  const citations = output.citations ?? [];
  const tokenCount = totalTokens(result.token_usage);
  const fareTotal = output.fare_total ?? itinerary?.transportation_cost;

  return (
    <div className="play-result">
      <div className="play-result-heading">
        <div>
          <span className={`play-status ${statusClass(output.status)}`}>
            {statusLabel(output.status)}
          </span>
          {result.execution_mode === "stub" && (
            <span className="play-stub-badge">Stub 実行</span>
          )}
        </div>
        <code>{result.run_id}</code>
      </div>

      <dl className="play-run-meta">
        <div>
          <dt>エージェント</dt>
          <dd>{result.agent_name || "—"}</dd>
        </div>
        <div>
          <dt>バージョン</dt>
          <dd>{result.agent_version || "—"}</dd>
        </div>
        <div>
          <dt>所要時間</dt>
          <dd>{formatDuration(result.duration_ms)}</dd>
        </div>
        <div>
          <dt>トークン合計</dt>
          <dd>
            {tokenCount === null
              ? "—"
              : Math.round(tokenCount).toLocaleString("ja-JP")}
          </dd>
        </div>
      </dl>

      <section className="play-output-section">
        <h3>追加確認事項</h3>
        {questions.length > 0 ? (
          <ul className="play-question-list">
            {questions.map((question, index) => (
              <li key={`${question}-${index}`}>{question}</li>
            ))}
          </ul>
        ) : (
          <EmptyValue>追加の確認事項はありません。</EmptyValue>
        )}
      </section>

      <section className="play-output-section">
        <h3>リクエスト概要</h3>
        {request ? (
          <dl className="play-detail-grid">
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
          <EmptyValue>リクエスト概要はまだ生成されていません。</EmptyValue>
        )}
      </section>

      <section className="play-output-section">
        <div className="play-section-heading">
          <h3>旅程・交通費</h3>
          <strong>{formatCurrency(fareTotal)}</strong>
        </div>
        {itinerary ? (
          <>
            <dl className="play-detail-grid">
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
                <dt>目的</dt>
                <dd>{itinerary.purpose || "—"}</dd>
              </div>
              <div>
                <dt>交通費</dt>
                <dd>{formatCurrency(itinerary.transportation_cost)}</dd>
              </div>
              <div>
                <dt>旅程合計</dt>
                <dd>{formatCurrency(itinerary.total_cost)}</dd>
              </div>
            </dl>
            {itinerary.transportation_legs.length > 0 ? (
              <ol className="play-leg-list">
                {itinerary.transportation_legs.map((leg, index) => (
                  <li key={`${leg.from}-${leg.to}-${index}`}>
                    <div className="play-leg-heading">
                      <strong>
                        {leg.method}: {leg.from} → {leg.to}
                      </strong>
                      <span>{formatCurrency(leg.cost)}</span>
                    </div>
                    <p>
                      {[leg.direction, leg.fare_type]
                        .filter(Boolean)
                        .join("・") || "運賃種別なし"}
                    </p>
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
              <EmptyValue>交通区間はありません。</EmptyValue>
            )}
          </>
        ) : (
          <EmptyValue>旅程はまだ生成されていません。</EmptyValue>
        )}
      </section>

      <section className="play-output-section">
        <div className="play-section-heading">
          <h3>ポリシー判定</h3>
          <span
            className={`play-status ${
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
            {policy.details.length > 0 ? (
              <ul className="play-detail-list">
                {policy.details.map((detail, index) => (
                  <li key={`${detail}-${index}`}>{detail}</li>
                ))}
              </ul>
            ) : (
              <EmptyValue>ポリシー詳細はありません。</EmptyValue>
            )}
          </>
        ) : (
          <EmptyValue>ポリシー判定はまだ生成されていません。</EmptyValue>
        )}
      </section>

      <section className="play-output-section">
        <h3>申請ドラフト</h3>
        {output.application_draft ? (
          <pre className="play-draft">{output.application_draft}</pre>
        ) : (
          <EmptyValue>申請ドラフトはありません。</EmptyValue>
        )}
      </section>

      <section className="play-output-section">
        <h3>引用・根拠</h3>
        {citations.length > 0 ? (
          <ul className="play-citation-list">
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
          <EmptyValue>引用・根拠はありません。</EmptyValue>
        )}
      </section>
    </div>
  );
}

function ScenarioCard({
  scenario,
  state,
  disabled,
  onRun,
}: {
  scenario: ScenarioDefinition;
  state: ScenarioState;
  disabled: boolean;
  onRun: () => void;
}) {
  return (
    <article className="play-scenario-card">
      <header className="play-scenario-header">
        <div>
          <p className="play-eyebrow">{scenario.eyebrow}</p>
          <h2>{scenario.title}</h2>
          <p>{scenario.description}</p>
        </div>
        <button
          type="button"
          className="play-button play-button-primary"
          onClick={onRun}
          disabled={disabled || state.isRunning}
        >
          {state.isRunning ? "実行中..." : "このシナリオを実行"}
        </button>
      </header>

      {state.error && (
        <div className="play-alert" role="alert">
          {state.error}
        </div>
      )}

      {state.result ? (
        <CanonicalOutput result={state.result} />
      ) : (
        !state.error && (
          <div className="play-result-placeholder">
            自然言語の依頼を入力し、このシナリオを実行すると結果が表示されます。
          </div>
        )
      )}
    </article>
  );
}

export function ScenarioPlayground() {
  const [input, setInput] = useState("");
  const [states, setStates] =
    useState<Record<EvaluationScenario, ScenarioState>>(INITIAL_STATE);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isRunningBoth, setIsRunningBoth] = useState(false);

  const executeScenario = useCallback(
    async (scenario: EvaluationScenario) => {
      const request = input.trim();
      if (!request) {
        setValidationError("出張リクエストを入力してください。");
        return;
      }

      setValidationError(null);
      setStates((current) => ({
        ...current,
        [scenario]: {
          ...current[scenario],
          error: null,
          isRunning: true,
        },
      }));
      try {
        const result = await runScenario({ scenario, input: request });
        setStates((current) => ({
          ...current,
          [scenario]: { result, error: null, isRunning: false },
        }));
      } catch (error) {
        setStates((current) => ({
          ...current,
          [scenario]: {
            ...current[scenario],
            error: errorMessage(error),
            isRunning: false,
          },
        }));
      }
    },
    [input],
  );

  const executeBoth = async () => {
    if (!input.trim()) {
      setValidationError("出張リクエストを入力してください。");
      return;
    }
    setIsRunningBoth(true);
    try {
      for (const scenario of SCENARIOS) {
        await executeScenario(scenario.id);
      }
    } finally {
      setIsRunningBoth(false);
    }
  };

  const anyRunning = Object.values(states).some((state) => state.isRunning);

  return (
    <>
      <section className="play-input-card">
        <div className="play-input-heading">
          <div>
            <p className="play-eyebrow">Manual scenario testing</p>
            <h2>自然言語で出張依頼を入力</h2>
            <p>
              申請の保存や送信は行いません。入力は各シナリオで共通のまま保持されます。
            </p>
          </div>
          <button
            type="button"
            className="play-button play-button-secondary"
            onClick={() => void executeBoth()}
            disabled={isRunningBoth || anyRunning}
          >
            {isRunningBoth ? "順番に実行中..." : "両方を順番に実行"}
          </button>
        </div>
        <label className="play-field">
          <span>出張リクエスト</span>
          <textarea
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
              if (validationError) setValidationError(null);
            }}
            maxLength={16000}
            rows={5}
            placeholder="例：10月15日に東京から大阪へ日帰りで出張したい。午前10時の顧客会議に間に合う新幹線を調べて、申請案を作成してください。"
          />
        </label>
        {validationError && (
          <p className="play-validation-error" role="alert">
            {validationError}
          </p>
        )}
      </section>

      <div className="play-scenario-grid">
        {SCENARIOS.map((scenario) => (
          <ScenarioCard
            key={scenario.id}
            scenario={scenario}
            state={states[scenario.id]}
            disabled={isRunningBoth || anyRunning}
            onRun={() => void executeScenario(scenario.id)}
          />
        ))}
      </div>
    </>
  );
}
