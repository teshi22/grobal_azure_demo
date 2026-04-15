"use client";

import { useState } from "react";

interface Props {
  question: string;
  missingFields: string[];
  disabled?: boolean;
  onSubmit: (answer: string) => void;
}

export function ClarificationForm({ question, missingFields, disabled, onSubmit }: Props) {
  const [answer, setAnswer] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!answer.trim() || disabled) return;
    onSubmit(answer.trim());
    setAnswer("");
  };

  return (
    <div className={`rounded-xl border border-amber-200 bg-amber-50 p-5 ${disabled ? "opacity-70" : ""}`}>
      <h3 className="mb-2 text-base font-semibold">❓ 追加情報が必要です</h3>

      {missingFields.length > 0 && (
        <div className="mb-3">
          <p className="text-xs text-gray-500">不足項目:</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {missingFields.map((f) => (
              <span
                key={f}
                className="rounded-full bg-amber-200 px-2 py-0.5 text-xs"
              >
                {f}
              </span>
            ))}
          </div>
        </div>
      )}

      <p className="mb-3 text-sm">{question}</p>

      {!disabled && (
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="回答を入力..."
            className="flex-1 rounded-lg border px-3 py-2 text-sm focus:border-amber-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={!answer.trim()}
            className="rounded-lg bg-amber-600 px-4 py-2 text-sm text-white hover:bg-amber-700 disabled:bg-gray-300"
          >
            回答
          </button>
        </form>
      )}
      {disabled && (
        <p className="text-xs text-gray-500">✅ 回答済み</p>
      )}
    </div>
  );
}
