"use client";

import { useCallback, useRef, useState } from "react";
import {
  createConversation,
  createEventSource,
  sendMessage,
} from "@/lib/api";
import type { ChatMessage, HITLRequestEvent, PolicyResultData, CompletionData } from "@/lib/types";

/** チャット状態管理フック — SSE 接続 + REST 応答 + 再接続 */
export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [hitlRequest, setHitlRequest] = useState<HITLRequestEvent | null>(
    null,
  );
  const eventSourceRef = useRef<EventSource | null>(null);

  const addMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const connectSSE = useCallback(
    (convId: string) => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }

      const es = createEventSource(convId);
      eventSourceRef.current = es;

      es.addEventListener("status", (e) => {
        const data = JSON.parse(e.data);
        addMessage({
          id: `status-${Date.now()}`,
          role: "system",
          content: data.label,
          timestamp: new Date(),
          eventType: "status",
        });
      });

      es.addEventListener("agent_response", (e) => {
        const data = JSON.parse(e.data);
        addMessage({
          id: `agent-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          role: "assistant",
          content: data.content,
          timestamp: new Date(),
          eventType: "agent_response",
        });
      });

      es.addEventListener("policy_result", (e) => {
        const data = JSON.parse(e.data) as PolicyResultData;
        addMessage({
          id: `policy-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          role: "assistant",
          content: "",
          timestamp: new Date(),
          eventType: "policy_result",
          policyResult: data,
        });
      });

      es.addEventListener("hitl_request", (e) => {
        const data = JSON.parse(e.data) as HITLRequestEvent;
        // メッセージ配列に追加 (チャット履歴に残す)
        addMessage({
          id: `hitl-${Date.now()}`,
          role: "assistant",
          content: "",
          timestamp: new Date(),
          eventType: "hitl_request",
          hitlData: data,
        });
        setHitlRequest(data);
        setIsLoading(false);
      });

      es.addEventListener("complete", (e) => {
        const data = JSON.parse(e.data);
        const completion: CompletionData = {
          output: data.output,
          requestId: data.request_id,
          plan: data.plan,
          policyDisplay: data.policy_display,
        };
        addMessage({
          id: `complete-${Date.now()}`,
          role: "assistant",
          content: data.output,
          timestamp: new Date(),
          eventType: "complete",
          completionData: completion,
        });
        setIsLoading(false);
        setHitlRequest(null);
        es.close();
      });

      es.addEventListener("error", (e) => {
        if (e instanceof MessageEvent) {
          const data = JSON.parse(e.data);
          addMessage({
            id: `error-${Date.now()}`,
            role: "system",
            content: `エラー: ${data.message}`,
            timestamp: new Date(),
            eventType: "error",
          });
        }
        setIsLoading(false);
      });
    },
    [addMessage],
  );

  const send = useCallback(
    async (content: string) => {
      setIsLoading(true);

      addMessage({
        id: `user-${Date.now()}`,
        role: "user",
        content,
        timestamp: new Date(),
      });

      try {
        let convId = conversationId;
        if (!convId) {
          const conv = await createConversation("dev-user");
          convId = conv.conversation_id;
          setConversationId(convId);
          connectSSE(convId);
        }

        const idempotencyKey = `${convId}-${Date.now()}`;
        await sendMessage(convId, content, idempotencyKey);

        if (hitlRequest) {
          // 現在のHITLメッセージを responded に変更 (カードは残るがボタン無効化)
          setMessages((prev) =>
            prev.map((msg) =>
              msg.hitlData && !msg.responded ? { ...msg, responded: true } : msg,
            ),
          );
          setHitlRequest(null);
        }
      } catch (err) {
        addMessage({
          id: `error-${Date.now()}`,
          role: "system",
          content: `送信エラー: ${err}`,
          timestamp: new Date(),
          eventType: "error",
        });
        setIsLoading(false);
      }
    },
    [conversationId, connectSSE, addMessage, hitlRequest],
  );

  return { messages, isLoading, hitlRequest, send };
}
