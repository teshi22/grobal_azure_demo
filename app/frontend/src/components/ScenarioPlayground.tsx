"use client";

import { useCallback, useRef, useState } from "react";
import {
  ChatWindow,
  type ChatWindowHandle,
  type ChatWindowState,
} from "./ChatWindow";
import type { ConversationScenario } from "@/lib/types";

interface ScenarioDefinition {
  id: ConversationScenario;
  eyebrow: string;
  title: string;
  shortTitle: string;
  description: string;
}

const SCENARIOS: ScenarioDefinition[] = [
  {
    id: "agent_framework_workflow",
    eyebrow: "Scenario A",
    title: "Agent Framework ワークフロー",
    shortTitle: "Agent Framework",
    description:
      "複数ステップで依頼を整理し、人の確認を挟みながら旅程と申請案を作成します。",
  },
  {
    id: "single_prompt_agent",
    eyebrow: "Scenario B",
    title: "Single Prompt Agent",
    shortTitle: "Single Prompt Agent",
    description:
      "単一エージェントが同じ依頼を処理し、必要な場面で人に確認を求めます。",
  },
];

const EMPTY_PANEL_STATE: ChatWindowState = {
  hasStarted: false,
  isLoading: false,
  isWaitingForInput: false,
  hasError: false,
};

export function ScenarioPlayground() {
  const [input, setInput] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isStartingBoth, setIsStartingBoth] = useState(false);
  const [launchingScenario, setLaunchingScenario] =
    useState<ConversationScenario | null>(null);
  const [panelStates, setPanelStates] = useState<
    Record<ConversationScenario, ChatWindowState>
  >({
    agent_framework_workflow: EMPTY_PANEL_STATE,
    single_prompt_agent: EMPTY_PANEL_STATE,
  });
  const frameworkRef = useRef<ChatWindowHandle>(null);
  const singleAgentRef = useRef<ChatWindowHandle>(null);

  const handleFor = (scenario: ConversationScenario) =>
    scenario === "agent_framework_workflow"
      ? frameworkRef.current
      : singleAgentRef.current;

  const requestValue = () => {
    const request = input.trim();
    if (!request) {
      setValidationError("出張リクエストを入力してください。");
      return null;
    }
    setValidationError(null);
    return request;
  };

  const startScenario = async (
    scenario: ConversationScenario,
    request = requestValue(),
  ) => {
    if (!request) return false;
    setLaunchingScenario(scenario);
    try {
      return (await handleFor(scenario)?.start(request)) ?? false;
    } finally {
      setLaunchingScenario(null);
    }
  };

  const startBoth = async () => {
    const request = requestValue();
    if (!request) return;
    setIsStartingBoth(true);
    try {
      await startScenario("agent_framework_workflow", request);
      await startScenario("single_prompt_agent", request);
    } finally {
      setIsStartingBoth(false);
    }
  };

  const updatePanelState = useCallback(
    (scenario: ConversationScenario, state: ChatWindowState) => {
      setPanelStates((current) =>
        current[scenario].hasStarted === state.hasStarted &&
        current[scenario].isLoading === state.isLoading &&
        current[scenario].isWaitingForInput === state.isWaitingForInput &&
        current[scenario].hasError === state.hasError
          ? current
          : { ...current, [scenario]: state },
      );
    },
    [],
  );
  const updateFrameworkState = useCallback(
    (state: ChatWindowState) =>
      updatePanelState("agent_framework_workflow", state),
    [updatePanelState],
  );
  const updateSingleAgentState = useCallback(
    (state: ChatWindowState) =>
      updatePanelState("single_prompt_agent", state),
    [updatePanelState],
  );

  const bothIdle = SCENARIOS.every(
    ({ id }) => !panelStates[id].hasStarted && !panelStates[id].isLoading,
  );

  return (
    <>
      <section className="play-input-card">
        <div className="play-input-heading">
          <div>
            <p className="play-eyebrow">2-scenario interactive HITL</p>
            <h2>同じ依頼から 2 つの対話を開始</h2>
            <p>
              共通の依頼で比較を始めた後は、確認への回答や追加メッセージを各パネルで個別に送れます。
            </p>
          </div>
          <button
            type="button"
            className="play-button play-button-primary"
            onClick={() => void startBoth()}
            disabled={!bothIdle || isStartingBoth}
          >
            {isStartingBoth ? "順番に開始中..." : "両方を順番に開始"}
          </button>
        </div>

        <label className="play-field">
          <span>最初の出張リクエスト</span>
          <textarea
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
              if (validationError) setValidationError(null);
            }}
            disabled={isStartingBoth}
            maxLength={16000}
            rows={4}
            placeholder="例：10月15日に東京から大阪へ日帰りで出張したい。午前10時の顧客会議に間に合う新幹線を調べて、申請案を作成してください。"
          />
        </label>

        <div className="play-start-actions">
          {SCENARIOS.map((scenario) => {
            const state = panelStates[scenario.id];
            const isLaunching = launchingScenario === scenario.id;
            return (
              <button
                key={scenario.id}
                type="button"
                className="play-button play-button-secondary"
                onClick={() => void startScenario(scenario.id)}
                disabled={
                  isStartingBoth ||
                  isLaunching ||
                  state.hasStarted ||
                  state.isLoading
                }
              >
                {isLaunching
                  ? "開始中..."
                  : state.hasStarted
                    ? `${scenario.shortTitle} は開始済み`
                    : `${scenario.shortTitle} のみ開始`}
              </button>
            );
          })}
        </div>

        {validationError && (
          <p className="play-validation-error" role="alert">
            {validationError}
          </p>
        )}
      </section>

      <div className="play-scenario-grid">
        {SCENARIOS.map((scenario) => (
          <ChatWindow
            key={scenario.id}
            ref={
              scenario.id === "agent_framework_workflow"
                ? frameworkRef
                : singleAgentRef
            }
            embedded
            eyebrow={scenario.eyebrow}
            title={scenario.title}
            description={scenario.description}
            controlsDisabled={isStartingBoth}
            conversationOptions={{
              scenario: scenario.id,
              interaction_mode: "playground",
            }}
            onStateChange={
              scenario.id === "agent_framework_workflow"
                ? updateFrameworkState
                : updateSingleAgentState
            }
          />
        ))}
      </div>
    </>
  );
}
