import { ScenarioApplication } from "@/components/ScenarioApplication";
import Link from "next/link";

export default function Home() {
  return (
    <main className="play-page">
      <header className="play-page-header">
        <div>
          <Link href="/" className="play-brand">
            AI 出張申請エージェント
          </Link>
          <h1>2シナリオ HITL 出張申請</h1>
          <p>
            Agent FrameworkとSingle Prompt Agentを試し、どちらからでもMCP経由で申請できます。
          </p>
        </div>
        <nav aria-label="主要ナビゲーション" className="play-page-nav">
          <Link href="/" aria-current="page">
            シナリオ申請
          </Link>
          <Link href="/requests/">申請一覧</Link>
          <Link href="/evaluations/">高度な一括評価</Link>
        </nav>
      </header>
      <ScenarioApplication />
    </main>
  );
}
