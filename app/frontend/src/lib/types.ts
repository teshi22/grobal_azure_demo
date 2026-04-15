/** 共有型定義 — SSE イベント / メッセージ */

export type SSEEventType =
  | "status"
  | "agent_response"
  | "hitl_request"
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
  type: "plan_review" | "clarification" | "request_confirmation";
  data: Record<string, unknown>;
  message: string;
}

export interface CompleteEvent {
  output: string;
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
}
