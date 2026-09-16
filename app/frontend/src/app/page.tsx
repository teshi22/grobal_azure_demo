"use client";

import { ChatWindow } from "@/components/ChatWindow";
import Link from "next/link";

export default function Home() {
  return (
    <main className="home-page">
      <header className="home-page-header">
        <div>
          <p className="home-workflow-label">Interactive HITL workflow</p>
          <h1>🛫 AI 出張申請エージェント</h1>
          <p>
            対話で内容を確認しながら、実際の出張申請を作成・送信するワークフローです。
          </p>
        </div>
        <nav
          className="home-page-nav"
          aria-label="主要ナビゲーション"
        >
          <Link href="/" aria-current="page">
            HITL 申請
          </Link>
          <Link href="/playground/">
            シナリオを試す
          </Link>
          <Link href="/requests/">申請一覧</Link>
          <Link href="/evaluations/">高度な一括評価</Link>
        </nav>
      </header>
      <ChatWindow />
    </main>
  );
}
