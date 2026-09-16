"use client";

import { useState } from "react";
import type {
  EvaluationRun,
  EvaluationScenario,
  EvaluationScenarioRun,
} from "@/lib/types";
import { EVALUATION_DATASET_ID } from "@/lib/types";

interface EvaluationRunsProps {
  runs: EvaluationRun[];
  selectedCaseIds: string[];
  selectedRunId: string | null;
  isLoading: boolean;
  error: string | null;
  onReload: () => Promise<void>;
  onStart: (caseIds: string[], name: string) => Promise<EvaluationRun>;
  onSelectRun: (runId: string) => void;
  onOpenResults: (runId: string) => void;
}

const SCENARIOS: Array<{
  id: EvaluationScenario;
  label: string;
  shortLabel: string;
}> = [
  {
    id: "agent_framework_workflow",
    label: "Agent Framework ワークフロー",
    shortLabel: "シナリオ A",
  },
  {
    id: "single_prompt_agent",
    label: "単一プロンプト エージェント",
    shortLabel: "シナリオ B",
  },
];

function scenarioName(
  scenario: (typeof SCENARIOS)[number],
  run: EvaluationScenarioRun | undefined,
): string {
  return (
    run?.name ??
    run?.agent_name ??
    (typeof run?.scenario_name === "string" ? run.scenario_name : null) ??
    scenario.label
  );
}

function scenarioVersion(run: EvaluationScenarioRun | undefined): string {
  const version =
    run?.version ??
    run?.agent_version ??
    (typeof run?.scenario_version === "string" ? run.scenario_version : null);
  return version || "バージョン情報なし";
}

function statusLabel(status: string | undefined): string {
  switch ((status ?? "").toLowerCase()) {
    case "queued":
    case "pending":
      return "待機中";
    case "running":
    case "in_progress":
      return "実行中";
    case "completed":
    case "succeeded":
    case "success":
      return "完了";
    case "partial":
    case "partially_completed":
    case "partial_failure":
      return "一部完了";
    case "failed":
    case "error":
      return "失敗";
    case "cancelled":
    case "canceled":
      return "キャンセル";
    default:
      return status || "未開始";
  }
}

function statusTone(status: string | undefined): string {
  const value = (status ?? "").toLowerCase();
  if (["completed", "succeeded", "success"].includes(value)) return "is-success";
  if (
    ["failed", "error", "cancelled", "canceled"].includes(value)
  ) {
    return "is-error";
  }
  if (["partial", "partially_completed", "partial_failure"].includes(value)) {
    return "is-warning";
  }
  if (["running", "in_progress", "queued", "pending"].includes(value)) {
    return "is-active";
  }
  return "";
}

function runMessage(run: EvaluationRun): string | null {
  const statuses = SCENARIOS.map(
    (scenario) => run.scenario_runs[scenario.id]?.status?.toLowerCase() ?? "",
  );
  const failed = statuses.filter((status) =>
    ["failed", "error", "cancelled", "canceled"].includes(status),
  ).length;
  const succeeded = statuses.filter((status) =>
    ["completed", "succeeded", "success"].includes(status),
  ).length;
  const overall = run.status.toLowerCase();
  if (
    ["partial", "partially_completed", "partial_failure"].includes(overall) ||
    (failed > 0 && succeeded > 0)
  ) {
    return "一方のシナリオのみ完了しています。結果は部分データとして表示されます。";
  }
  if (
    ["failed", "error"].includes(overall) ||
    failed === SCENARIOS.length
  ) {
    return "両シナリオの評価に失敗しました。各シナリオのエラーを確認してください。";
  }
  return null;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("ja-JP");
}

export function EvaluationRuns({
  runs,
  selectedCaseIds,
  selectedRunId,
  isLoading,
  error,
  onReload,
  onStart,
  onSelectRun,
  onOpenResults,
}: EvaluationRunsProps) {
  const [name, setName] = useState("");
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const startRun = async () => {
    if (selectedCaseIds.length === 0) {
      setStartError("テストケースタブで有効なケースを 1 件以上選択してください。");
      return;
    }
    setIsStarting(true);
    setStartError(null);
    try {
      const run = await onStart(
        selectedCaseIds,
        name.trim() || `比較評価 ${new Date().toLocaleString("ja-JP")}`,
      );
      setName("");
      onSelectRun(run.id);
    } catch (startRunError) {
      setStartError(
        startRunError instanceof Error
          ? startRunError.message
          : "評価ランを開始できませんでした。",
      );
    } finally {
      setIsStarting(false);
    }
  };

  return (
    <section aria-labelledby="runs-heading" className="eval-section">
      <div className="eval-section-heading">
        <div>
          <p className="eval-eyebrow">同期実行</p>
          <h2 id="runs-heading">評価ラン</h2>
          <p>
            同じケースを、固定された 2 つのシナリオで同時に評価します。
          </p>
        </div>
        <button className="eval-button eval-button-secondary" onClick={onReload}>
          更新
        </button>
      </div>

      <div className="eval-run-launch">
        <div>
          <strong>新しい比較評価</strong>
          <p>
            対象: 有効な選択済みケース {selectedCaseIds.length} 件
          </p>
        </div>
        <label className="eval-field eval-field-grow">
          <span>ラン名（任意）</span>
          <input
            value={name}
            maxLength={160}
            onChange={(event) => setName(event.target.value)}
            placeholder="例: リリース前回帰テスト"
          />
        </label>
        <button
          className="eval-button eval-button-primary"
          disabled={isStarting || selectedCaseIds.length === 0}
          onClick={() => void startRun()}
        >
          {isStarting ? "開始中…" : "2 シナリオを開始"}
        </button>
      </div>
      {startError && (
        <div className="eval-alert eval-alert-error">{startError}</div>
      )}
      {error && <div className="eval-alert eval-alert-error">{error}</div>}

      <div className="eval-scenario-pins" aria-label="固定シナリオ">
        {SCENARIOS.map((scenario) => (
          <div key={scenario.id}>
            <span>{scenario.shortLabel}</span>
            <strong>{scenario.label}</strong>
            <small>名前とバージョンはラン作成時に固定されます</small>
          </div>
        ))}
      </div>

      {isLoading ? (
        <div className="eval-empty" aria-live="polite">
          読み込み中…
        </div>
      ) : runs.length === 0 ? (
        <div className="eval-empty">
          評価ランはまだありません。ケースを選択して最初のランを開始してください。
        </div>
      ) : (
        <div className="eval-run-list">
          {runs.map((run) => {
            const message = runMessage(run);
            return (
              <article
                key={run.id}
                className={`eval-run-card ${
                  selectedRunId === run.id ? "is-selected" : ""
                }`}
              >
                <div className="eval-run-card-heading">
                  <div>
                    <span
                      className={`eval-status ${statusTone(run.status)}`}
                    >
                      {statusLabel(run.status)}
                    </span>
                    <h3>{run.name || run.id}</h3>
                    <p>
                      {formatDate(run.created_at)} ・ 固定データセット{" "}
                      {EVALUATION_DATASET_ID} / {run.dataset_version || "—"}
                    </p>
                  </div>
                  <div className="eval-actions">
                    <button
                      className="eval-button eval-button-secondary"
                      onClick={() => onSelectRun(run.id)}
                    >
                      選択
                    </button>
                    <button
                      className="eval-button eval-button-primary"
                      onClick={() => onOpenResults(run.id)}
                    >
                      比較結果
                    </button>
                  </div>
                </div>
                {message && (
                  <div
                    className={`eval-alert ${
                      message.startsWith("一方")
                        ? "eval-alert-warning"
                        : "eval-alert-error"
                    }`}
                  >
                    {message}
                  </div>
                )}
                <div className="eval-run-scenarios">
                  {SCENARIOS.map((scenario) => {
                    const scenarioRun = run.scenario_runs[scenario.id];
                    const scenarioError =
                      scenarioRun?.error ??
                      (typeof scenarioRun?.last_sync_error === "string"
                        ? scenarioRun.last_sync_error
                        : null) ??
                      (typeof scenarioRun?.message === "string"
                        ? scenarioRun.message
                        : null);
                    return (
                      <div key={scenario.id}>
                        <div className="eval-scenario-heading">
                          <span>{scenario.shortLabel}</span>
                          <span
                            className={`eval-status ${statusTone(
                              scenarioRun?.status,
                            )}`}
                          >
                            {statusLabel(scenarioRun?.status)}
                          </span>
                        </div>
                        <strong>{scenarioName(scenario, scenarioRun)}</strong>
                        <code>{scenarioVersion(scenarioRun)}</code>
                        {scenarioError && (
                          <p className="eval-error-text">{scenarioError}</p>
                        )}
                      </div>
                    );
                  })}
                </div>
                <div className="eval-run-meta">
                  <span>Run ID: {run.id}</span>
                  {run.foundry_evaluation_id && (
                    <span>Foundry: {run.foundry_evaluation_id}</span>
                  )}
                </div>
                {run.error && !message && (
                  <div className="eval-alert eval-alert-error">{run.error}</div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
