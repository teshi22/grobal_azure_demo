/** 共有型定義 — SSE イベント / メッセージ / 出張申請 */

export type SSEEventType =
  | "status"
  | "agent_response"
  | "hitl_request"
  | "policy_result"
  | "complete"
  | "error";

export interface StatusEvent {
  step: string;
  label: string;
}

export interface AgentResponseEvent {
  step: string;
  content: string;
}

export interface HITLRequestEvent {
  type: "plan_review" | "clarification" | "request_confirmation" | "submit_confirmation";
  data: Record<string, unknown>;
  message: string;
}

export interface PolicyResultData {
  compliant: boolean;
  details: string[];
  summary: string;
  message?: string;
}

export interface CompleteEvent {
  output: string;
  request_id?: string;
  plan?: Record<string, unknown>;
  policy_display?: string;
}

/** 完了画面の構造化データ */
export interface CompletionData {
  output: string;
  requestId?: string;
  plan?: Record<string, unknown>;
  policyDisplay?: string;
}

export interface ErrorEvent {
  message: string;
  step?: string;
}

/** チャットメッセージ (表示用) */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: Date;
  eventType?: SSEEventType;
  hitlData?: HITLRequestEvent;
  policyResult?: PolicyResultData;
  completionData?: CompletionData;
  responded?: boolean;
}

export type ConversationScenario =
  | "agent_framework_workflow"
  | "single_prompt_agent";

export type ConversationInteractionMode = "submission" | "playground";

export interface CreateConversationOptions {
  scenario: ConversationScenario;
  interaction_mode: ConversationInteractionMode;
}

/** 出張申請データ */
export interface TravelRequest {
  id: string;
  request_id: string;
  conversation_id: string;
  status: string;
  submitted_at: string;
  departure: string;
  destination: string;
  schedule: string;
  purpose: string;
  trip_type: string;
  transportation_legs: TransportationLeg[];
  transportation_cost: number;
  hotel: string;
  hotel_cost_per_night: number;
  hotel_nights: number;
  total_cost: number;
}

export interface TransportationLeg {
  method: string;
  from: string;
  to: string;
  cost?: number | null;
  direction?: string;
  fareType?: string;
  sourceUrl?: string;
  sourceTitle?: string;
}

/** 2 シナリオ比較評価 */
export const EVALUATION_DATASET_ID = "travel-request-v1" as const;

export type EvaluationStatus =
  | "needs_clarification"
  | "draft_ready"
  | "policy_blocked"
  | "error";

export type EvaluationScenario =
  | "agent_framework_workflow"
  | "single_prompt_agent";

export type HumanWinner = EvaluationScenario | "tie";

export interface ExpectedEvaluationRequest {
  departure: string;
  destination: string;
  schedule: string;
  purpose: string;
}

export interface EvaluationCaseInput {
  id: string;
  dataset_id: typeof EVALUATION_DATASET_ID;
  title: string;
  input: string;
  expected_status: EvaluationStatus;
  expected_request: ExpectedEvaluationRequest;
  expected_policy_compliant: boolean | null;
  expected_clarification_fields: string[];
  tags: string[];
  enabled: boolean;
}

export interface EvaluationCase extends EvaluationCaseInput {
  version: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export type EvaluationCaseUpdate = Omit<EvaluationCaseInput, "id"> & {
  version?: number;
};

export interface EvaluationLeg {
  direction?: string;
  method: string;
  from: string;
  to: string;
  cost: number;
  fare_type: string;
  source_url: string;
  source_title: string;
}

export interface EvaluationItinerary {
  departure: string;
  destination: string;
  purpose: string;
  schedule: string;
  trip_type: "日帰り" | "宿泊";
  transportation_legs: EvaluationLeg[];
  transportation_cost: number;
  total_cost: number;
  [key: string]: unknown;
}

export interface EvaluationPolicyOutput {
  compliant: boolean | null;
  details: string[];
  narrative: string;
}

export interface EvaluationCitation {
  url: string;
  title: string;
  fare_type: string;
}

export interface CanonicalEvaluationOutput {
  status: EvaluationStatus;
  request?: ExpectedEvaluationRequest | null;
  clarification_questions?: string[];
  itinerary?: EvaluationItinerary | null;
  fare_total?: number | null;
  policy?: EvaluationPolicyOutput;
  application_draft?: string;
  citations?: EvaluationCitation[];
  diagnostics?: {
    schema_version: string;
    scenario: EvaluationScenario;
    case_id: string;
    agent_versions: Record<string, string>;
  };
}

/** 手動シナリオ実行 */
export type ScenarioExecutionMode = "foundry" | "stub";

export interface ScenarioRunRequest {
  scenario: EvaluationScenario;
  input: string;
}

export interface ScenarioRunResponse {
  run_id: string;
  scenario: EvaluationScenario;
  agent_name: string;
  agent_version: string;
  execution_mode: ScenarioExecutionMode;
  duration_ms: number;
  token_usage: Record<string, unknown>;
  output: CanonicalEvaluationOutput;
}

export interface DeterministicCheck {
  name: string;
  passed: boolean;
  score: number;
  detail: string;
}

export interface HumanReview {
  agent_framework_score: number;
  single_agent_score: number;
  winner: HumanWinner;
  comment: string;
  reviewer_id?: string;
  reviewer_name?: string;
  reviewed_at?: string | null;
}

export interface EvaluationResult {
  comparison_id: string;
  case_id: string;
  scenario: EvaluationScenario;
  output: CanonicalEvaluationOutput | Record<string, unknown>;
  rubric_score: number | null;
  deterministic_score: number | null;
  overall_score: number | null;
  deterministic_checks: DeterministicCheck[];
  evaluator_scores: Record<string, string | number | null>;
  token_usage: Record<string, unknown>;
  estimated_cost: Record<string, unknown>;
  raw_output?: unknown;
  error?: string;
  human_review?: HumanReview | null;
}

export interface EvaluationCaseComparison {
  case: EvaluationCase;
  agent_framework_workflow: EvaluationResult | null;
  single_prompt_agent: EvaluationResult | null;
  scenario_statuses?: Partial<Record<EvaluationScenario, string>>;
  scenario_errors?: Partial<Record<EvaluationScenario, string>>;
  human_review?: HumanReview | null;
  version?: number;
}

export interface EvaluationScenarioRun {
  status: string;
  name?: string;
  version?: string;
  agent_name?: string;
  agent_version?: string;
  duration_ms?: number | null;
  token_usage?: Record<string, unknown>;
  estimated_cost?: Record<string, unknown>;
  error?: string | null;
  [key: string]: unknown;
}

export interface EvaluationRun {
  id: string;
  name: string;
  status: string;
  dataset_id: string;
  dataset_version: string;
  foundry_evaluation_id: string;
  scenario_runs: Partial<
    Record<EvaluationScenario, EvaluationScenarioRun>
  >;
  case_ids?: string[];
  aggregate: Record<string, unknown>;
  pricing_updated_at: string;
  error?: string;
  version?: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface EvaluationImportResponse {
  imported?: number;
  created?: number;
  updated?: number;
  cases?: EvaluationCase[];
  [key: string]: unknown;
}
