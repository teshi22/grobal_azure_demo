"use client";

import type { PolicyResultData } from "@/lib/types";

interface Props {
  data: PolicyResultData;
}

export function PolicyResultCard({ data }: Props) {
  const isCompliant = data.compliant;

  const bgClass = isCompliant
    ? "border-green-200 bg-green-50"
    : "border-red-200 bg-red-50";
  const textClass = isCompliant ? "text-green-800" : "text-red-800";
  const icon = isCompliant ? "✅" : "❌";
  const titleColor = isCompliant ? "text-green-700" : "text-red-700";

  return (
    <div className={`rounded-xl border p-5 shadow-sm ${bgClass}`}>
      <h3 className={`mb-3 text-base font-semibold ${titleColor}`}>
        {icon} {data.summary}
      </h3>

      <div className={`space-y-1 text-sm ${textClass}`}>
        {data.details.map((line, i) => (
          <p key={i} className="ml-2">
            {line}
          </p>
        ))}
      </div>

      {data.message && (
        <p className={`mt-3 text-sm font-medium ${titleColor}`}>
          🔄 {data.message}
        </p>
      )}
    </div>
  );
}
