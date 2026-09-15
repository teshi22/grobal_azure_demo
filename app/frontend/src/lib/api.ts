/** API クライアント */

import type { TravelRequest } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

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
  const res = await fetch(`${API_BASE}/travel-requests`);
  if (!res.ok)
    throw new Error(`Failed to fetch travel requests: ${res.status}`);
  return res.json();
}

export async function fetchTravelRequest(
  requestId: string,
): Promise<TravelRequest> {
  const res = await fetch(`${API_BASE}/travel-requests/${requestId}`);
  if (!res.ok)
    throw new Error(`Failed to fetch travel request: ${res.status}`);
  return res.json();
}
