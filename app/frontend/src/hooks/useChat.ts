"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createConversation,
  createEventSource,
  sendMessage,
} from "@/lib/api";
import type {
  ChatMessage,
  CompletionData,
  CreateConversationOptions,
  HITLRequestEvent,
  PolicyResultData,
  StatusEvent,
} from "@/lib/types";

interface RetryState {
  content: string;
  idempotencyKey?: string;
}

function messageFromError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "通信に失敗しました。時間をおいて再度お試しください。";
}

/** チャット状態管理フック — 会話ごとに独立した SSE 接続と再試行状態を持つ */
export function useChat(conversationOptions?: CreateConversationOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [hitlRequest, setHitlRequest] = useState<HITLRequestEvent | null>(null);
  const [status, setStatus] = useState<StatusEvent | null>(null);
  const [isComplete, setIsComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef("");
  const conversationIdRef = useRef<string | null>(null);
  const isLoadingRef = useRef(false);
  const hitlRequestRef = useRef<HITLRequestEvent | null>(null);
  const pendingHitlMessageIdRef = useRef<string | null>(null);
  const retryRef = useRef<RetryState | null>(null);
  const generationRef = useRef(0);

  const setLoading = useCallback((value: boolean) => {
    isLoadingRef.current = value;
    setIsLoading(value);
  }, []);

  const addMessage = useCallback((message: ChatMessage) => {
    setMessages((current) => [...current, message]);
  }, []);

  const closeSSE = useCallback(() => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
  }, []);

  const connectSSE = useCallback(
    (convId: string, generation: number) => {
      closeSSE();
      const es = createEventSource(
        convId,
        lastEventIdRef.current || undefined,
      );
      eventSourceRef.current = es;

      const isCurrent = () => generationRef.current === generation;
      const rememberEvent = (event: MessageEvent) => {
        lastEventIdRef.current =
          event.lastEventId || lastEventIdRef.current;
      };

      es.addEventListener("status", (event) => {
        if (!isCurrent()) return;
        const messageEvent = event as MessageEvent;
        rememberEvent(messageEvent);
        const data = JSON.parse(messageEvent.data) as StatusEvent;
        setStatus(data);
        addMessage({
          id: `status-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          role: "system",
          content: data.label,
          timestamp: new Date(),
          eventType: "status",
        });
      });

      es.addEventListener("agent_response", (event) => {
        if (!isCurrent()) return;
        const messageEvent = event as MessageEvent;
        rememberEvent(messageEvent);
        const data = JSON.parse(messageEvent.data) as {
          step?: string;
          content: string;
        };
        addMessage({
          id: `agent-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          role: "assistant",
          content: data.content,
          timestamp: new Date(),
          eventType: "agent_response",
        });
        hitlRequestRef.current = null;
        pendingHitlMessageIdRef.current = null;
        setHitlRequest(null);
        setStatus({ step: data.step || "ready", label: "入力できます" });
        setIsComplete(false);
        retryRef.current = null;
        setCanRetry(false);
        setLoading(false);
        closeSSE();
      });

      es.addEventListener("policy_result", (event) => {
        if (!isCurrent()) return;
        const messageEvent = event as MessageEvent;
        rememberEvent(messageEvent);
        const data = JSON.parse(messageEvent.data) as PolicyResultData;
        addMessage({
          id: `policy-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          role: "assistant",
          content: "",
          timestamp: new Date(),
          eventType: "policy_result",
          policyResult: data,
        });
      });

      es.addEventListener("hitl_request", (event) => {
        if (!isCurrent()) return;
        const messageEvent = event as MessageEvent;
        rememberEvent(messageEvent);
        const data = JSON.parse(messageEvent.data) as HITLRequestEvent;
        const messageId =
          `hitl-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
        addMessage({
          id: messageId,
          role: "assistant",
          content: "",
          timestamp: new Date(),
          eventType: "hitl_request",
          hitlData: data,
        });
        hitlRequestRef.current = data;
        pendingHitlMessageIdRef.current = messageId;
        setHitlRequest(data);
        setStatus({
          step: data.type,
          label: "あなたの入力を待っています",
        });
        retryRef.current = null;
        setCanRetry(false);
        setLoading(false);
        closeSSE();
      });

      es.addEventListener("complete", (event) => {
        if (!isCurrent()) return;
        const messageEvent = event as MessageEvent;
        rememberEvent(messageEvent);
        const data = JSON.parse(messageEvent.data) as {
          output: string;
          request_id?: string;
          plan?: Record<string, unknown>;
          policy_display?: string;
        };
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
        hitlRequestRef.current = null;
        pendingHitlMessageIdRef.current = null;
        setHitlRequest(null);
        setStatus({ step: "complete", label: "完了" });
        setIsComplete(true);
        retryRef.current = null;
        setCanRetry(false);
        setLoading(false);
        closeSSE();
      });

      es.addEventListener("error", (event) => {
        if (!isCurrent()) return;
        if (event instanceof MessageEvent) {
          rememberEvent(event);
          const data = JSON.parse(event.data) as { message?: string };
          const detail = data.message || "ストリームでエラーが発生しました。";
          setError(detail);
          addMessage({
            id: `error-${Date.now()}`,
            role: "system",
            content: `エラー: ${detail}`,
            timestamp: new Date(),
            eventType: "error",
          });
          setStatus({ step: "error", label: "エラー" });
          setCanRetry(retryRef.current !== null);
          closeSSE();
        }
        setLoading(false);
      });
    },
    [addMessage, closeSSE, setLoading],
  );

  const reset = useCallback(() => {
    generationRef.current += 1;
    closeSSE();
    conversationIdRef.current = null;
    hitlRequestRef.current = null;
    pendingHitlMessageIdRef.current = null;
    retryRef.current = null;
    lastEventIdRef.current = "";
    isLoadingRef.current = false;
    setMessages([]);
    setConversationId(null);
    setHitlRequest(null);
    setStatus(null);
    setIsComplete(false);
    setError(null);
    setCanRetry(false);
    setIsLoading(false);
  }, [closeSSE]);

  useEffect(() => closeSSE, [closeSSE]);

  const performSend = useCallback(
    async (
      content: string,
      appendUserMessage: boolean,
      retryKey?: string,
    ): Promise<boolean> => {
      const trimmed = content.trim();
      if (!trimmed || isLoadingRef.current) return false;

      const hitlMessageBeingAnswered = pendingHitlMessageIdRef.current;
      setLoading(true);
      setError(null);
      setCanRetry(false);
      setIsComplete(false);
      setStatus({
        step: hitlMessageBeingAnswered ? "hitl_response" : "message",
        label: hitlMessageBeingAnswered
          ? "回答を送信中..."
          : "リクエストを送信中...",
      });
      if (appendUserMessage) {
        addMessage({
          id: `user-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          role: "user",
          content: trimmed,
          timestamp: new Date(),
        });
      }
      const generation = generationRef.current;
      let idempotencyKey = retryKey;
      try {
        let convId = conversationIdRef.current;
        if (!convId) {
          const conversation = await createConversation(conversationOptions);
          if (generationRef.current !== generation) return false;
          convId = conversation.conversation_id;
          conversationIdRef.current = convId;
          setConversationId(convId);
        }

        idempotencyKey ??=
          `${convId}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        retryRef.current = { content: trimmed, idempotencyKey };
        connectSSE(convId, generation);
        await sendMessage(convId, trimmed, idempotencyKey);
        if (generationRef.current !== generation) return false;

        retryRef.current = { content: trimmed };
        setCanRetry(false);
        if (hitlMessageBeingAnswered) {
          setMessages((current) =>
            current.map((message) =>
              message.id === hitlMessageBeingAnswered
                ? { ...message, responded: true }
                : message,
            ),
          );
          if (pendingHitlMessageIdRef.current === hitlMessageBeingAnswered) {
            pendingHitlMessageIdRef.current = null;
            hitlRequestRef.current = null;
            setHitlRequest(null);
          }
        }
        return true;
      } catch (sendError) {
        if (generationRef.current !== generation) return false;
        closeSSE();
        const detail = messageFromError(sendError);
        retryRef.current = { content: trimmed, idempotencyKey };
        setError(detail);
        setCanRetry(true);
        setStatus({ step: "error", label: "送信エラー" });
        setLoading(false);
        return false;
      }
    },
    [
      addMessage,
      closeSSE,
      connectSSE,
      conversationOptions,
      setLoading,
    ],
  );

  const send = useCallback(
    (content: string) => performSend(content, true),
    [performSend],
  );

  const retry = useCallback(async (): Promise<boolean> => {
    const pending = retryRef.current;
    if (!pending) return false;
    return performSend(pending.content, false, pending.idempotencyKey);
  }, [performSend]);

  return {
    messages,
    isLoading,
    conversationId,
    hitlRequest,
    status,
    isComplete,
    error,
    canRetry,
    send,
    retry,
    reset,
  };
}
