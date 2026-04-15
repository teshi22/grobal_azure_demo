/** API クライアント */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export interface Conversation {
  conversation_id: string;
  status: string;
}

export interface MessageResult {
  message_id: string;
  status: string;
}

export async function createConversation(
  userId: string,
): Promise<Conversation> {
  const res = await fetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
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
    headers["X-Idempotency-Key"] = idempotencyKey;
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
  const url = `${API_BASE}/conversations/${conversationId}/stream`;
  const es = new EventSource(url);
  return es;
}
