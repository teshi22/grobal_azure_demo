"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { useChat } from "@/hooks/useChat";
import type { CreateConversationOptions } from "@/lib/types";
import { MessageBubble } from "./MessageBubble";
import { PlanConfirmCard } from "./PlanConfirmCard";
import { RequestConfirmCard } from "./RequestConfirmCard";
import { ClarificationForm } from "./ClarificationForm";
import { SubmitConfirmCard } from "./SubmitConfirmCard";
import { PolicyResultCard } from "./PolicyResultCard";
import { CompletionCard } from "./CompletionCard";

export interface ChatWindowState {
  hasStarted: boolean;
  isLoading: boolean;
  isWaitingForInput: boolean;
  hasError: boolean;
}

export interface ChatWindowHandle {
  start: (content: string) => Promise<boolean>;
  reset: () => void;
}

interface ChatWindowProps {
  conversationOptions?: CreateConversationOptions;
  embedded?: boolean;
  eyebrow?: string;
  title?: string;
  description?: string;
  controlsDisabled?: boolean;
  onStateChange?: (state: ChatWindowState) => void;
}

export const ChatWindow = forwardRef<ChatWindowHandle, ChatWindowProps>(
  function ChatWindow(
    {
      conversationOptions,
      embedded = false,
      eyebrow,
      title,
      description,
      controlsDisabled = false,
      onStateChange,
    },
    ref,
  ) {
    const chat = useChat(conversationOptions);
    const [input, setInput] = useState("");
    const [initialRequest, setInitialRequest] = useState<string | null>(null);
    const bottomRef = useRef<HTMLDivElement>(null);
    const hasStarted = chat.conversationId !== null || chat.messages.length > 0;
    const isWaitingForInput = chat.hitlRequest !== null;

    useEffect(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [chat.messages, chat.isLoading]);

    useEffect(() => {
      onStateChange?.({
        hasStarted,
        isLoading: chat.isLoading,
        isWaitingForInput,
        hasError: chat.error !== null,
      });
    }, [
      chat.error,
      chat.isLoading,
      hasStarted,
      isWaitingForInput,
      onStateChange,
    ]);

    const sendContent = async (
      content: string,
      allowExternalStart = false,
    ) => {
      const trimmed = content.trim();
      if (
        !trimmed ||
        chat.isLoading ||
        (controlsDisabled && !allowExternalStart)
      ) {
        return false;
      }
      if (!initialRequest) setInitialRequest(trimmed);
      return chat.send(trimmed);
    };

    const resetPanel = () => {
      chat.reset();
      setInput("");
      setInitialRequest(null);
    };

    const restartPanel = async () => {
      if (!initialRequest || chat.isLoading || controlsDisabled) return false;
      chat.reset();
      setInput("");
      return chat.send(initialRequest);
    };

    useImperativeHandle(
      ref,
      () => ({
        start: (content) => sendContent(content, true),
        reset: resetPanel,
      }),
    );

    const handleSubmit = (event: React.FormEvent) => {
      event.preventDefault();
      const content = input.trim();
      if (!content) return;
      setInput("");
      void sendContent(content);
    };

    const stageLabel = isWaitingForInput
      ? "あなたの入力を待っています"
      : chat.isLoading
        ? chat.status?.label || "処理中..."
        : chat.error
          ? "エラー"
          : chat.isComplete
            ? "完了"
            : hasStarted
              ? "入力できます"
              : "未開始";

    return (
      <section
        className={`chat-shell ${embedded ? "chat-shell-embedded" : ""}`}
      >
        {embedded && (
          <header className="chat-panel-header">
            <div className="chat-panel-heading">
              <div>
                {eyebrow && <p className="play-eyebrow">{eyebrow}</p>}
                {title && <h2>{title}</h2>}
                {description && <p>{description}</p>}
              </div>
              <div className="chat-panel-actions">
                <button
                  type="button"
                  className="chat-secondary-button"
                  onClick={() => void restartPanel()}
                  disabled={
                    !initialRequest || chat.isLoading || controlsDisabled
                  }
                >
                  再スタート
                </button>
                <button
                  type="button"
                  className="chat-secondary-button"
                  onClick={resetPanel}
                  disabled={!hasStarted || chat.isLoading || controlsDisabled}
                >
                  リセット
                </button>
              </div>
            </div>
            <div className="chat-status-row" aria-live="polite">
              <span
                className={`chat-status-dot ${
                  chat.isLoading
                    ? "is-processing"
                    : isWaitingForInput
                      ? "is-waiting"
                      : chat.error
                        ? "is-error"
                        : chat.isComplete
                          ? "is-complete"
                          : ""
                }`}
              />
              <strong>{stageLabel}</strong>
              {chat.status?.step && (
                <code>{chat.status.step}</code>
              )}
            </div>
          </header>
        )}

        <div className="chat-timeline" aria-live="polite">
          {chat.messages.length === 0 && (
            <div className="chat-empty-state">
              <span aria-hidden="true">💬</span>
              <strong>会話はまだ始まっていません</strong>
              <p>
                {embedded
                  ? "上の共通リクエストからこのシナリオを開始してください。"
                  : "下の入力欄から出張リクエストを送信してください。"}
              </p>
            </div>
          )}

          {chat.messages.map((message) => {
            if (message.policyResult) {
              return (
                <PolicyResultCard key={message.id} data={message.policyResult} />
              );
            }
            if (message.completionData) {
              return (
                <CompletionCard
                  key={message.id}
                  data={message.completionData}
                  isPlayground={
                    conversationOptions?.interaction_mode === "playground"
                  }
                />
              );
            }
            if (message.hitlData) {
              const disabled = Boolean(message.responded) || chat.isLoading;
              const { hitlData } = message;
              if (hitlData.type === "request_confirmation") {
                return (
                  <RequestConfirmCard
                    key={message.id}
                    data={hitlData.data}
                    disabled={disabled}
                    onConfirm={() => void sendContent("OK")}
                    onRevise={(feedback) => void sendContent(feedback)}
                  />
                );
              }
              if (hitlData.type === "plan_review") {
                return (
                  <PlanConfirmCard
                    key={message.id}
                    data={hitlData.data}
                    disabled={disabled}
                    onConfirm={() => void sendContent("OK")}
                    onRevise={(feedback) => void sendContent(feedback)}
                  />
                );
              }
              if (hitlData.type === "submit_confirmation") {
                return (
                  <SubmitConfirmCard
                    key={message.id}
                    data={hitlData.data}
                    disabled={disabled}
                    onConfirm={() => void sendContent("OK")}
                    onCancel={() => void sendContent("キャンセル")}
                  />
                );
              }
              if (hitlData.type === "clarification") {
                return (
                  <ClarificationForm
                    key={message.id}
                    question={
                      (hitlData.data.question as string) || hitlData.message
                    }
                    missingFields={
                      (hitlData.data.missing_fields as string[]) || []
                    }
                    disabled={disabled}
                    onSubmit={(answer) => void sendContent(answer)}
                  />
                );
              }
              return null;
            }
            return <MessageBubble key={message.id} message={message} />;
          })}

          {chat.isLoading && (
            <div className="chat-processing">
              <span />
              {chat.status?.label || "エージェントが処理しています..."}
            </div>
          )}

          {chat.error && (
            <div className="chat-error" role="alert">
              <div>
                <strong>送信できませんでした</strong>
                <p>{chat.error}</p>
              </div>
              {chat.canRetry && (
                <button
                  type="button"
                  className="chat-secondary-button"
                  onClick={() => void chat.retry()}
                  disabled={chat.isLoading || controlsDisabled}
                >
                  再試行
                </button>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <form onSubmit={handleSubmit} className="chat-composer">
          <input
            type="text"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={
              isWaitingForInput
                ? "確認への回答を入力..."
                : hasStarted
                  ? "続けてメッセージを入力..."
                  : "出張リクエストを入力..."
            }
            disabled={chat.isLoading || controlsDisabled}
            aria-label="メッセージ"
          />
          <button
            type="submit"
            className="chat-primary-button"
            disabled={
              chat.isLoading || controlsDisabled || !input.trim()
            }
          >
            送信
          </button>
        </form>
      </section>
    );
  },
);
