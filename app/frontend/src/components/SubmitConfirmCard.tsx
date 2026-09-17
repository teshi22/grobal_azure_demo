"use client";

import { useState } from "react";

interface Props {
  data: Record<string, unknown>;
  disabled?: boolean;
  decision?: "approved" | "rejected";
  onConfirm: () => void;
  onCancel: () => void;
}

export function SubmitConfirmCard({
  data,
  disabled,
  decision,
  onConfirm,
  onCancel,
}: Props) {
  const [checked, setChecked] = useState(false);
  const policyResult = (data.policy_result as string) || "";
  const applicationText = (data.application_text as string) || "";

  return (
    <article className={`hitl-card ${disabled ? "is-disabled" : ""}`}>
      <h3>📤 出張申請の送信確認</h3>
      <div className="hitl-policy-box">
        <strong>📋 旅費規程チェック結果</strong>
        {policyResult.split("\n").map((line, index) => (
          <p key={`${line}-${index}`}>{line}</p>
        ))}
      </div>
      {applicationText && (
        <details className="hitl-policy-box">
          <summary>申請書案を確認</summary>
          <pre className="completion-output">{applicationText}</pre>
        </details>
      )}
      {!disabled && (
        <label className="hitl-check-field">
          <input
            type="checkbox"
            checked={checked}
            onChange={(event) => setChecked(event.target.checked)}
          />
          <span>上記の内容で出張申請を送信します</span>
        </label>
      )}
      <div className="hitl-actions">
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled || !checked}
          className="chat-primary-button"
        >
          {disabled
            ? decision === "approved"
              ? "実行を承認しました"
              : decision === "rejected"
                ? "実行を拒否しました"
                : "回答済み"
            : "申請する"}
        </button>
        {!disabled && (
          <button
            type="button"
            onClick={onCancel}
            className="chat-secondary-button"
          >
            キャンセル
          </button>
        )}
      </div>
    </article>
  );
}
