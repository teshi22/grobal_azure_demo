"use client";

import type { PolicyResultData } from "@/lib/types";

interface Props {
  data: PolicyResultData;
}

export function PolicyResultCard({ data }: Props) {
  return (
    <article
      className={`chat-info-card ${
        data.compliant ? "is-success" : "is-error"
      }`}
    >
      <h3>
        {data.compliant ? "✓" : "!"} {data.summary}
      </h3>
      <div>
        {data.details.map((line, index) => (
          <p key={`${line}-${index}`}>{line}</p>
        ))}
      </div>
      {data.message && <strong>↻ {data.message}</strong>}
    </article>
  );
}
