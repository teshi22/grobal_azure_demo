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
  cost: number;
}
