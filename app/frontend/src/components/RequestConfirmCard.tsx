"use client";

import { useState } from "react";

interface Props {
  data: Record<string, unknown>;
  disabled?: boolean;
  onConfirm: () => void;
  onRevise: (feedback: string) => void;
}

const FIELD_CONFIG = [
  { key: "departure", icon: "📍", label: "出発地" },
  { key: "destination", icon: "🏢", label: "目的地" },
  { key: "schedule", icon: "📅", label: "日程" },
  { key: "purpose", icon: "🎯", label: "目的" },
] as const;

export function RequestConfirmCard({
  data,
  disabled,
  onConfirm,
  onRevise,
}: Props) {
  const [isRevising, setIsRevising] = useState(false);
  const [feedback, setFeedback] = useState("");

  const submitRevision = (event: React.FormEvent) => {
    event.preventDefault();
    if (!feedback.trim() || disabled) return;
    onRevise(feedback.trim());
    setFeedback("");
    setIsRevising(false);
  };

  return (
    <article className={`hitl-card ${disabled ? "is-disabled" : ""}`}>
      <h3>📝 リクエスト内容の確認</h3>
      <dl className="hitl-detail-grid">
        {FIELD_CONFIG.map(({ key, icon, label }) => {
          const value = data[key];
          if (typeof value !== "string" || !value) return null;
          return (
            <div key={key}>
              <dt>
                {icon} {label}
              </dt>
              <dd>{value}</dd>
            </div>
          );
        })}
      </dl>
      {!disabled && (
        <p className="hitl-help">
          この内容で旅程を検索します。内容を確認してください。
        </p>
      )}
      {!disabled && isRevising && (
        <form className="hitl-inline-form" onSubmit={submitRevision}>
          <input
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="修正内容を入力..."
            autoFocus
            aria-label="リクエストの修正内容"
          />
          <button
            type="submit"
            className="chat-primary-button"
            disabled={!feedback.trim()}
          >
            修正を送信
          </button>
        </form>
      )}
      <div className="hitl-actions">
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled}
          className="chat-primary-button"
        >
          {disabled ? "✓ 確認済み" : "この内容で検索"}
        </button>
        {!disabled && (
          <button
            type="button"
            onClick={() => setIsRevising((value) => !value)}
            className="chat-secondary-button"
          >
            修正する
          </button>
        )}
      </div>
    </article>
  );
}
