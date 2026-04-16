"use client";

import { useState } from "react";

interface Props {
  data: Record<string, unknown>;
  disabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function SubmitConfirmCard({ data, disabled, onConfirm, onCancel }: Props) {
  const [checked, setChecked] = useState(false);
  const policyResult = (data.policy_result as string) || "";

  return (
    <div className={`rounded-xl border bg-white p-5 shadow-sm ${disabled ? "opacity-70" : ""}`}>
      <h3 className="mb-3 text-base font-semibold">📤 出張申請の送信確認</h3>

      <div className="mb-3 rounded-lg bg-green-50 px-4 py-3 text-sm text-green-800">
        <p className="mb-1 font-medium">📋 旅費規程チェック結果</p>
        {policyResult.split("\n").map((line, i) => (
          <p key={i} className="ml-2">{line}</p>
        ))}
      </div>

      {!disabled && (
        <label className="mb-4 flex cursor-pointer items-center gap-3 rounded-lg border px-4 py-3 hover:bg-gray-50">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
            className="h-5 w-5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <span className="text-sm font-medium">
            上記の内容で出張申請を送信します
          </span>
        </label>
      )}

      <div className="flex gap-3">
        <button
          onClick={onConfirm}
          disabled={disabled || !checked}
          className="rounded-lg bg-blue-600 px-5 py-2 text-sm text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {disabled ? "✅ 申請済み" : "📤 申請する"}
        </button>
        {!disabled && (
          <button
            onClick={onCancel}
            className="rounded-lg border px-5 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            キャンセル
          </button>
        )}
      </div>
    </div>
  );
}
