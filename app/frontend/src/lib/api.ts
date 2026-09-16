/** API クライアント */

import type {
  EvaluationCase,
  EvaluationCaseComparison,
  EvaluationCaseInput,
  EvaluationCaseUpdate,
  EvaluationImportResponse,
  EvaluationResult,
  EvaluationRun,
  EvaluationScenario,
  HumanReview,
  TravelRequest,
} from "./types";
import { EVALUATION_DATASET_ID } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as {
        detail?: unknown;
        message?: string;
      };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        detail = body.detail
          .map((item) =>
            item &&
            typeof item === "object" &&
            "msg" in item &&
            typeof item.msg === "string"
              ? item.msg
              : "",
          )
          .filter(Boolean)
          .join("、");
      } else if (
        body.detail &&
        typeof body.detail === "object" &&
        "message" in body.detail &&
        typeof body.detail.message === "string"
      ) {
        detail = body.detail.message;
      } else if (body.message) {
        detail = body.message;
      }
    } catch {
      detail = await res.text().catch(() => "");
    }
    throw new ApiRequestError(
      detail || `API request failed: ${res.status}`,
      res.status,
    );
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function unwrapList<T>(
  value: unknown,
  keys: string[],
): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of keys) {
      if (Array.isArray(record[key])) return record[key] as T[];
    }
  }
  return [];
}

function requireEvaluationDataset<T extends { dataset_id: string }>(
  value: T,
): T {
  if (value.dataset_id !== EVALUATION_DATASET_ID) {
    throw new Error(
      `Unsupported evaluation dataset: ${value.dataset_id || "(empty)"}`,
    );
  }
  return value;
}

function scenarioResult(
  value: unknown,
  scenario: EvaluationScenario,
): EvaluationResult | null {
  if (!value || typeof value !== "object") return null;
  const result = value as EvaluationResult;
  return { ...result, scenario: result.scenario || scenario };
}

function normalizeComparison(value: unknown): EvaluationCaseComparison {
  const item = value as Record<string, unknown>;
  const nestedResults =
    item.results && typeof item.results === "object"
      ? (item.results as Record<string, unknown>)
      : item.scenarios && typeof item.scenarios === "object"
        ? (item.scenarios as Record<string, unknown>)
      : {};
  const scenarioA =
    item.agent_framework_workflow ??
    nestedResults.agent_framework_workflow ??
    item.scenario_a;
  const scenarioB =
    item.single_prompt_agent ??
    nestedResults.single_prompt_agent ??
    item.scenario_b;
  const resultA = scenarioResult(
    scenarioA,
    "agent_framework_workflow",
  );
  const resultB = scenarioResult(scenarioB, "single_prompt_agent");
  const caseId =
    (typeof item.case_id === "string" && item.case_id) ||
    resultA?.case_id ||
    resultB?.case_id ||
    "";
  const embeddedCase =
    item.case && typeof item.case === "object"
      ? (item.case as EvaluationCase)
      : {
          id: caseId,
          dataset_id: EVALUATION_DATASET_ID,
          title:
            typeof item.case_title === "string" ? item.case_title : caseId,
          input: typeof item.case_input === "string" ? item.case_input : "",
          expected_status: "draft_ready" as const,
          expected_request: {
            departure: "",
            destination: "",
            schedule: "",
            purpose: "",
          },
          expected_policy_compliant: null,
          expected_clarification_fields: [],
          tags: [],
          enabled: true,
          version: 0,
        };
  return {
    case: embeddedCase,
    agent_framework_workflow: resultA,
    single_prompt_agent: resultB,
    scenario_statuses:
      item.scenario_statuses &&
      typeof item.scenario_statuses === "object"
        ? (item.scenario_statuses as Partial<
            Record<EvaluationScenario, string>
          >)
        : undefined,
    scenario_errors:
      item.scenario_errors && typeof item.scenario_errors === "object"
        ? (item.scenario_errors as Partial<
            Record<EvaluationScenario, string>
          >)
        : undefined,
    human_review:
      (item.human_review as HumanReview | null | undefined) ??
      resultA?.human_review ??
      resultB?.human_review ??
      null,
    version: typeof item.version === "number" ? item.version : undefined,
  };
}

export interface Conversation {
  conversation_id: string;
  status: string;
}

export interface MessageResult {
  message_id: string;
  status: string;
}

export async function createConversation(): Promise<Conversation> {
  const res = await fetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`Failed to create conversation: ${res.status}`);
  return res.json();
}

export async function sendMessage(
  conversationId: string,
  content: string,
  idempotencyKey?: string,
): Promise<MessageResult> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }
  const res = await fetch(
    `${API_BASE}/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({ content }),
    },
  );
  if (!res.ok) throw new Error(`Failed to send message: ${res.status}`);
  return res.json();
}

export function createEventSource(
  conversationId: string,
  lastEventId?: string,
): EventSource {
  const params = new URLSearchParams();
  if (lastEventId) {
    params.set("last_event_id", lastEventId);
  }
  const query = params.size > 0 ? `?${params.toString()}` : "";
  const url = `${API_BASE}/conversations/${conversationId}/stream${query}`;
  const es = new EventSource(url);
  return es;
}

export async function fetchTravelRequests(): Promise<TravelRequest[]> {
  return apiRequest<TravelRequest[]>("/travel-requests");
}

export async function fetchTravelRequest(
  requestId: string,
): Promise<TravelRequest> {
  return apiRequest<TravelRequest>(`/travel-requests/${requestId}`);
}

export async function fetchEvaluationCases(): Promise<EvaluationCase[]> {
  const params = new URLSearchParams({
    dataset_id: EVALUATION_DATASET_ID,
  });
  const data = await apiRequest<unknown>(
    `/evaluations/cases?${params.toString()}`,
  );
  return unwrapList<EvaluationCase>(data, ["cases", "items"]).filter(
    (item) => item.dataset_id === EVALUATION_DATASET_ID,
  );
}

export async function createEvaluationCase(
  value: EvaluationCaseInput,
): Promise<EvaluationCase> {
  const created = await apiRequest<EvaluationCase>("/evaluations/cases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...value,
      dataset_id: EVALUATION_DATASET_ID,
    }),
  });
  return requireEvaluationDataset(created);
}

export async function updateEvaluationCase(
  id: string,
  value: EvaluationCaseUpdate,
): Promise<EvaluationCase> {
  const updated = await apiRequest<EvaluationCase>(
    `/evaluations/cases/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...value,
        dataset_id: EVALUATION_DATASET_ID,
      }),
    },
  );
  return requireEvaluationDataset(updated);
}

export async function importEvaluationCases(
  cases: EvaluationCaseInput[],
): Promise<EvaluationImportResponse> {
  return apiRequest<EvaluationImportResponse>("/evaluations/cases/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      cases: cases.map((item) => ({
        ...item,
        dataset_id: EVALUATION_DATASET_ID,
      })),
    }),
  });
}

export async function fetchEvaluationRuns(): Promise<EvaluationRun[]> {
  const data = await apiRequest<unknown>("/evaluations/runs");
  return unwrapList<EvaluationRun>(data, ["runs", "items"]).filter(
    (item) => item.dataset_id === EVALUATION_DATASET_ID,
  );
}

export async function createEvaluationRun(
  caseIds: string[],
  name: string,
): Promise<EvaluationRun> {
  const run = await apiRequest<EvaluationRun>("/evaluations/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dataset_id: EVALUATION_DATASET_ID,
      case_ids: caseIds,
      name,
    }),
  });
  return requireEvaluationDataset(run);
}

export async function fetchEvaluationRun(
  id: string,
): Promise<EvaluationRun> {
  const run = await apiRequest<EvaluationRun>(
    `/evaluations/runs/${encodeURIComponent(id)}`,
  );
  return requireEvaluationDataset(run);
}

export async function fetchEvaluationResults(
  runId: string,
): Promise<EvaluationCaseComparison[]> {
  const data = await apiRequest<unknown>(
    `/evaluations/runs/${encodeURIComponent(runId)}/results`,
  );
  return unwrapList<unknown>(data, ["results", "comparisons", "items"]).map(
    normalizeComparison,
  );
}

export async function saveEvaluationReview(
  runId: string,
  caseId: string,
  review: HumanReview,
  version?: number,
): Promise<HumanReview> {
  type ReviewResponse =
    | HumanReview
    | { human_review?: HumanReview | null };
  const request = (path: string, body: unknown) =>
    apiRequest<ReviewResponse>(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  const path =
    `/evaluations/runs/${encodeURIComponent(runId)}/results/` +
    `${encodeURIComponent(caseId)}/review`;
  let data: ReviewResponse;
  try {
    data = await request(path, review);
  } catch (error) {
    if (
      !(error instanceof ApiRequestError) ||
      ![404, 405].includes(error.status)
    ) {
      throw error;
    }
    data = await request(
      path.replace(/\/review$/, "/human-review"),
      { ...review, version: version ?? 1 },
    );
  }
  if ("human_review" in data) {
    return data.human_review ?? review;
  }
  if (
    "agent_framework_score" in data &&
    "single_agent_score" in data &&
    "winner" in data
  ) {
    return data;
  }
  return review;
}
