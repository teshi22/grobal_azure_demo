"use client";

interface Props {
  enrichedRequest: string;
  onConfirm: () => void;
  onRevise: (feedback: string) => void;
}

/** enriched_request テキストを構造化フィールドに分解 */
function parseFields(text: string) {
  const iconMap: [RegExp, string, string][] = [
    [/出発地/, "📍", "出発地"],
    [/目的地/, "🏢", "目的地"],
    [/日程/, "📅", "日程"],
    [/(?:出張)?目的/, "🎯", "目的"],
  ];

  const parts = text
    .split(/[、。]/)
    .map((s) => s.trim())
    .filter(Boolean);

  return parts.map((part) => {
    for (const [re, icon, label] of iconMap) {
      if (re.test(part)) {
        const value = part
          .replace(/^.*?[はが:：]\s*/, "")
          .replace(/です$/, "");
        return { icon, label, value };
      }
    }
    return { icon: "📝", label: "", value: part.replace(/です$/, "") };
  });
}

export function RequestConfirmCard({
  enrichedRequest,
  onConfirm,
  onRevise,
}: Props) {
  const fields = parseFields(enrichedRequest);

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
        {fields.map((f, i) => (
          <div key={i} className="flex items-start gap-2">
            <span className="shrink-0">{f.icon}</span>
            <span>
              {f.label && (
                <span className="font-medium">{f.label}: </span>
              )}
              {f.value}
            </span>
          </div>
        ))}
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
