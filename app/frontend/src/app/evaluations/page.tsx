"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EvaluationCases } from "@/components/evaluations/EvaluationCases";
import { EvaluationResults } from "@/components/evaluations/EvaluationResults";
import { EvaluationRuns } from "@/components/evaluations/EvaluationRuns";
import {
  createEvaluationCase,
  createEvaluationRun,
  fetchEvaluationCases,
  fetchEvaluationResults,
  fetchEvaluationRun,
  fetchEvaluationRuns,
  importEvaluationCases,
  saveEvaluationReview,
  updateEvaluationCase,
} from "@/lib/api";
import type {
  EvaluationCase,
  EvaluationCaseComparison,
  EvaluationCaseInput,
  EvaluationCaseUpdate,
  EvaluationRun,
  HumanReview,
} from "@/lib/types";

type EvaluationTab = "cases" | "runs" | "results";

const ACTIVE_RUN_STATUSES = new Set([
  "queued",
  "pending",
  "running",
  "in_progress",
]);

function isActiveRun(run: EvaluationRun): boolean {
  if (ACTIVE_RUN_STATUSES.has(run.status.toLowerCase())) return true;
  return Object.values(run.scenario_runs).some((scenario) =>
    ACTIVE_RUN_STATUSES.has(scenario?.status?.toLowerCase() ?? ""),
  );
}

function upsertRun(runs: EvaluationRun[], value: EvaluationRun): EvaluationRun[] {
  const exists = runs.some((run) => run.id === value.id);
  return exists
    ? runs.map((run) => (run.id === value.id ? value : run))
    : [value, ...runs];
}

function errorMessage(value: unknown, fallback: string): string {
  return value instanceof Error ? value.message : fallback;
}

function comparisonCaseId(
  comparison: EvaluationCaseComparison,
): string | undefined {
  return (
    comparison.case?.id ??
    comparison.agent_framework_workflow?.case_id ??
    comparison.single_prompt_agent?.case_id
  );
}

function hydrateComparisonCases(
  comparisons: EvaluationCaseComparison[],
  cases: EvaluationCase[],
): EvaluationCaseComparison[] {
  const caseMap = new Map(cases.map((item) => [item.id, item]));
  return comparisons.flatMap((comparison) => {
    const caseId = comparisonCaseId(comparison);
    const evaluationCase =
      (caseId ? caseMap.get(caseId) : null) ?? comparison.case;
    return evaluationCase ? [{ ...comparison, case: evaluationCase }] : [];
  });
}

export default function EvaluationsPage() {
  const [activeTab, setActiveTab] = useState<EvaluationTab>("cases");
  const [cases, setCases] = useState<EvaluationCase[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [comparisons, setComparisons] = useState<
    EvaluationCaseComparison[]
  >([]);
  const [casesLoading, setCasesLoading] = useState(true);
  const [runsLoading, setRunsLoading] = useState(true);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [casesError, setCasesError] = useState<string | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const didInitializeSelection = useRef(false);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  const loadCases = useCallback(async () => {
    setCasesLoading(true);
    setCasesError(null);
    try {
      const data = await fetchEvaluationCases();
      setCases(data);
      setSelectedCaseIds((current) => {
        const enabledIds = new Set(
          data.filter((item) => item.enabled).map((item) => item.id),
        );
        if (!didInitializeSelection.current) {
          didInitializeSelection.current = true;
          return Array.from(enabledIds);
        }
        return current.filter((id) => enabledIds.has(id));
      });
    } catch (loadError) {
      setCasesError(
        errorMessage(loadError, "テストケースを取得できませんでした。"),
      );
    } finally {
      setCasesLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    setRunsError(null);
    try {
      const data = await fetchEvaluationRuns();
      setRuns(data);
      setSelectedRunId((current) => current ?? data[0]?.id ?? null);
    } catch (loadError) {
      setRunsError(
        errorMessage(loadError, "評価ランを取得できませんでした。"),
      );
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const loadResults = useCallback(
    async (runId: string, showLoading = true) => {
      if (showLoading) setResultsLoading(true);
      setResultsError(null);
      try {
        const [run, resultData] = await Promise.all([
          fetchEvaluationRun(runId),
          fetchEvaluationResults(runId),
        ]);
        setRuns((current) => upsertRun(current, run));
        setComparisons(hydrateComparisonCases(resultData, cases));
      } catch (loadError) {
        setResultsError(
          errorMessage(loadError, "比較結果を取得できませんでした。"),
        );
      } finally {
        if (showLoading) setResultsLoading(false);
      }
    },
    [cases],
  );

  useEffect(() => {
    void Promise.all([loadCases(), loadRuns()]);
  }, [loadCases, loadRuns]);

  const activeRunIds = useMemo(
    () => runs.filter(isActiveRun).map((run) => run.id),
    [runs],
  );
  const activeRunKey = activeRunIds.join("|");

  useEffect(() => {
    if (!activeRunKey) return;
    const ids = activeRunKey.split("|");
    let inFlight = false;
    let cancelled = false;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const updatedRuns = await Promise.all(
          ids.map((id) => fetchEvaluationRun(id)),
        );
        if (cancelled) return;
        setRuns((current) =>
          updatedRuns.reduce(
            (next, updated) => upsertRun(next, updated),
            current,
          ),
        );
        if (
          selectedRunId &&
          ids.includes(selectedRunId) &&
          activeTab === "results"
        ) {
          const resultData = await fetchEvaluationResults(selectedRunId);
          if (!cancelled) {
            setComparisons(hydrateComparisonCases(resultData, cases));
          }
        }
      } catch (pollError) {
        if (!cancelled) {
          setRunsError(
            errorMessage(pollError, "実行状況の自動更新に失敗しました。"),
          );
        }
      } finally {
        inFlight = false;
      }
    };

    const timer = window.setInterval(() => void poll(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeRunKey, activeTab, cases, selectedRunId]);

  const handleCreateCase = async (value: EvaluationCaseInput) => {
    const created = await createEvaluationCase(value);
    setCases((current) => [created, ...current]);
    if (created.enabled) {
      setSelectedCaseIds((current) =>
        Array.from(new Set([...current, created.id])),
      );
    }
  };

  const handleUpdateCase = async (
    id: string,
    value: EvaluationCaseUpdate,
  ) => {
    try {
      const updated = await updateEvaluationCase(id, value);
      setCases((current) =>
        current.map((item) => (item.id === id ? updated : item)),
      );
      if (!updated.enabled) {
        setSelectedCaseIds((current) =>
          current.filter((caseId) => caseId !== id),
        );
      }
      setCasesError(null);
    } catch (updateError) {
      setCasesError(
        errorMessage(updateError, "テストケースを更新できませんでした。"),
      );
      throw updateError;
    }
  };

  const handleImport = async (values: EvaluationCaseInput[]) => {
    try {
      await importEvaluationCases(values);
      await loadCases();
    } catch (importError) {
      setCasesError(
        errorMessage(importError, "テストケースをインポートできませんでした。"),
      );
      throw importError;
    }
  };

  const handleStartRun = async (caseIds: string[], name: string) => {
    const enabledIds = new Set(
      cases.filter((item) => item.enabled).map((item) => item.id),
    );
    const validIds = caseIds.filter((id) => enabledIds.has(id));
    if (validIds.length === 0) {
      throw new Error("有効なテストケースを 1 件以上選択してください。");
    }
    const run = await createEvaluationRun(validIds, name);
    setRuns((current) => upsertRun(current, run));
    setSelectedRunId(run.id);
    return run;
  };

  const handleSelectRun = async (runId: string) => {
    setSelectedRunId(runId);
    setRunsError(null);
    try {
      const run = await fetchEvaluationRun(runId);
      setRuns((current) => upsertRun(current, run));
    } catch (selectError) {
      setRunsError(
        errorMessage(selectError, "評価ランの詳細を取得できませんでした。"),
      );
    }
  };

  const handleOpenResults = (runId: string) => {
    setSelectedRunId(runId);
    setActiveTab("results");
    void loadResults(runId);
  };

  const handleTabChange = (tab: EvaluationTab) => {
    setActiveTab(tab);
    if (tab === "results" && selectedRunId) {
      void loadResults(selectedRunId);
    }
  };

  const handleSaveReview = async (caseId: string, review: HumanReview) => {
    if (!selectedRunId) throw new Error("評価ランが選択されていません。");
    const comparison = comparisons.find((item) => item.case.id === caseId);
    const saved = await saveEvaluationReview(
      selectedRunId,
      caseId,
      review,
      comparison?.version,
    );
    setComparisons((current) =>
      current.map((comparison) =>
        comparison.case.id === caseId
          ? {
              ...comparison,
              human_review: saved,
              agent_framework_workflow:
                comparison.agent_framework_workflow && {
                  ...comparison.agent_framework_workflow,
                  human_review: saved,
                },
              single_prompt_agent:
                comparison.single_prompt_agent && {
                  ...comparison.single_prompt_agent,
                  human_review: saved,
                },
              version:
                comparison.version === undefined
                  ? undefined
                  : comparison.version + 1,
            }
          : comparison,
      ),
    );
  };

  return (
    <main className="eval-page">
      <header className="eval-page-header">
        <div>
          <Link href="/" className="eval-brand">
            AI 出張申請エージェント
          </Link>
          <h1>デュアルシナリオ評価</h1>
          <p>
            高度な一括評価として、同じテストケースで 2
            つの実装を同期実行し、自動評価と人手評価を分けて比較します。
          </p>
        </div>
        <nav aria-label="主要ナビゲーション" className="eval-page-nav">
          <Link href="/">HITL 申請</Link>
          <Link href="/playground/">2シナリオHITL</Link>
          <Link href="/requests/">申請一覧</Link>
          <Link href="/evaluations/" aria-current="page">
            高度な一括評価
          </Link>
        </nav>
      </header>

      <div className="eval-tabs" role="tablist" aria-label="評価メニュー">
        {(
          [
            ["cases", "1. テストケース"],
            ["runs", "2. 評価ラン"],
            ["results", "3. 比較結果"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            id={`eval-tab-${id}`}
            role="tab"
            aria-selected={activeTab === id}
            aria-controls={`eval-panel-${id}`}
            onClick={() => handleTabChange(id)}
          >
            {label}
            {id === "cases" && <span>{selectedCaseIds.length}</span>}
            {id === "runs" && <span>{activeRunIds.length}</span>}
            {id === "results" && selectedRunId && <span>1</span>}
          </button>
        ))}
      </div>

      <div
        id={`eval-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`eval-tab-${activeTab}`}
        className="eval-panel"
      >
        {activeTab === "cases" && (
          <EvaluationCases
            cases={cases}
            selectedCaseIds={selectedCaseIds}
            isLoading={casesLoading}
            error={casesError}
            onReload={loadCases}
            onSelectionChange={setSelectedCaseIds}
            onCreate={handleCreateCase}
            onUpdate={handleUpdateCase}
            onImport={handleImport}
          />
        )}
        {activeTab === "runs" && (
          <EvaluationRuns
            runs={runs}
            selectedCaseIds={selectedCaseIds}
            selectedRunId={selectedRunId}
            isLoading={runsLoading}
            error={runsError}
            onReload={loadRuns}
            onStart={handleStartRun}
            onSelectRun={(runId) => void handleSelectRun(runId)}
            onOpenResults={handleOpenResults}
          />
        )}
        {activeTab === "results" && (
          <EvaluationResults
            run={selectedRun}
            comparisons={comparisons}
            isLoading={resultsLoading}
            error={resultsError}
            onReload={() =>
              selectedRunId
                ? loadResults(selectedRunId)
                : Promise.resolve()
            }
            onSaveReview={handleSaveReview}
          />
        )}
      </div>
    </main>
  );
}
