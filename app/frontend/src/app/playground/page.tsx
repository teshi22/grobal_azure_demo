import Link from "next/link";
import { ScenarioPlayground } from "@/components/ScenarioPlayground";

export default function PlaygroundPage() {
  return (
    <main className="play-page">
      <header className="play-page-header">
        <div>
          <Link href="/" className="play-brand">
            AI 出張申請エージェント
          </Link>
          <h1>2シナリオ HITL プレイグラウンド</h1>
          <p>
            2 つの旅行エージェントと並行して対話し、確認・修正・承認の流れを比較できます。
          </p>
        </div>
        <nav aria-label="主要ナビゲーション" className="play-page-nav">
          <Link href="/">HITL 申請</Link>
          <Link href="/playground/" aria-current="page">
            2シナリオHITL
          </Link>
          <Link href="/requests/">申請一覧</Link>
          <Link href="/evaluations/">高度な一括評価</Link>
        </nav>
      </header>

      <ScenarioPlayground />
    </main>
  );
}
