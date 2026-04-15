"use client";

interface Props {
  data: Record<string, unknown>;
  onConfirm: () => void;
  onRevise: (feedback: string) => void;
}

const FIELD_CONFIG = [
  { key: "departure", icon: "📍", label: "出発地" },
  { key: "destination", icon: "🏢", label: "目的地" },
  { key: "schedule", icon: "📅", label: "日程" },
  { key: "purpose", icon: "🎯", label: "目的" },
] as const;

export function RequestConfirmCard({ data, onConfirm, onRevise }: Props) {
  const handleRevise = () => {
    const feedback = prompt("修正内容を入力してください:");
    if (feedback) onRevise(feedback);
  };

  return (
    <div className="rounded-xl border bg-white p-5 shadow-sm">
      <h3 className="mb-3 text-base font-semibold">
        📝 リクエスト内容の確認
      </h3>

      <div className="space-y-2 text-sm">
        {FIELD_CONFIG.map(({ key, icon, label }) => {
          const value = data[key] as string | undefined;
          if (!value) return null;
          return (
            <div key={key} className="flex items-start gap-2">
              <span className="shrink-0">{icon}</span>
              <span>
                <span className="font-medium">{label}: </span>
                {value}
              </span>
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-xs text-gray-500">
        この内容で旅程を検索します。よろしいですか？
      </p>

      <div className="mt-4 flex gap-3">
        <button
          onClick={onConfirm}
          className="rounded-lg bg-blue-600 px-5 py-2 text-sm text-white hover:bg-blue-700"
        >
          ✅ この内容で検索
        </button>
        <button
          onClick={handleRevise}
          className="rounded-lg border px-5 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          ✏️ 修正する
        </button>
      </div>
    </div>
  );
}
