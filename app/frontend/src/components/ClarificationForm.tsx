"use client";

import { useState } from "react";

interface Props {
  question: string;
  missingFields: string[];
  disabled?: boolean;
  onSubmit: (answer: string) => void;
}

export function ClarificationForm({
  question,
  missingFields,
  disabled,
  onSubmit,
}: Props) {
  const [answer, setAnswer] = useState("");

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!answer.trim() || disabled) return;
    onSubmit(answer.trim());
    setAnswer("");
  };

  return (
    <article className={`hitl-card is-waiting ${disabled ? "is-disabled" : ""}`}>
      <h3>❓ 追加情報が必要です</h3>
      {missingFields.length > 0 && (
        <div className="hitl-field-list">
          <span>不足項目</span>
          <div>
            {missingFields.map((field) => (
              <strong key={field}>{field}</strong>
            ))}
          </div>
        </div>
      )}
      <p className="hitl-question">{question}</p>
      {!disabled ? (
        <form onSubmit={handleSubmit} className="hitl-inline-form">
          <input
            type="text"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="回答を入力..."
            aria-label="追加情報への回答"
          />
          <button
            type="submit"
            className="chat-primary-button"
            disabled={!answer.trim()}
          >
            回答
          </button>
        </form>
      ) : (
        <p className="hitl-responded">✓ 回答済み</p>
      )}
    </article>
  );
}
