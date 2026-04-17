"use client";

import { useState, useRef, useEffect } from "react";
import { useChat } from "@/hooks/useChat";
import { MessageBubble } from "./MessageBubble";
import { PlanConfirmCard } from "./PlanConfirmCard";
import { RequestConfirmCard } from "./RequestConfirmCard";
import { ClarificationForm } from "./ClarificationForm";
import { SubmitConfirmCard } from "./SubmitConfirmCard";
import { PolicyResultCard } from "./PolicyResultCard";

export function ChatWindow() {
  const { messages, isLoading, hitlRequest, send } = useChat();
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    send(input.trim());
    setInput("");
  };

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* メッセージ一覧 */}
      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
        {messages.map((msg) => {
          // PolicyResult カード
          if (msg.policyResult) {
            return <PolicyResultCard key={msg.id} data={msg.policyResult} />;
          }

          // HITL カードはメッセージ配列から描画
          if (msg.hitlData) {
            const disabled = !!msg.responded;
            const { hitlData } = msg;

            if (hitlData.type === "request_confirmation") {
              return (
                <RequestConfirmCard
                  key={msg.id}
                  data={hitlData.data}
                  disabled={disabled}
                  onConfirm={() => send("OK")}
                  onRevise={(feedback) => send(feedback)}
                />
              );
            }
            if (hitlData.type === "plan_review") {
              return (
                <PlanConfirmCard
                  key={msg.id}
                  data={hitlData.data}
                  disabled={disabled}
                  onConfirm={() => send("OK")}
                  onRevise={(feedback) => send(feedback)}
                />
              );
            }
            if (hitlData.type === "submit_confirmation") {
              return (
                <SubmitConfirmCard
                  key={msg.id}
                  data={hitlData.data}
                  disabled={disabled}
                  onConfirm={() => send("OK")}
                  onCancel={() => send("キャンセル")}
                />
              );
            }
            if (hitlData.type === "clarification") {
              return (
                <ClarificationForm
                  key={msg.id}
                  question={
                    (hitlData.data.question as string) || hitlData.message
                  }
                  missingFields={
                    (hitlData.data.missing_fields as string[]) || []
                  }
                  disabled={disabled}
                  onSubmit={(answer) => send(answer)}
                />
              );
            }
            return null;
          }

          return <MessageBubble key={msg.id} message={msg} />;
        })}

        {isLoading && (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <div className="h-2 w-2 animate-pulse rounded-full bg-blue-400" />
            処理中...
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 入力フォーム */}
      <form
        onSubmit={handleSubmit}
        className="border-t bg-white px-6 py-4"
      >
        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="出張リクエストを入力..."
            disabled={isLoading}
            className="flex-1 rounded-lg border px-4 py-2 focus:border-blue-500 focus:outline-none disabled:bg-gray-100"
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="rounded-lg bg-blue-600 px-6 py-2 text-white hover:bg-blue-700 disabled:bg-gray-300"
          >
            送信
          </button>
        </div>
      </form>
    </div>
  );
}
