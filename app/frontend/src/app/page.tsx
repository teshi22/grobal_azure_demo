"use client";

import { ChatWindow } from "@/components/ChatWindow";
import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-bold">🛫 AI 出張申請エージェント</h1>
          <p className="text-sm text-gray-500">
            出張リクエストを入力してください
          </p>
        </div>
        <nav
          className="ml-auto flex flex-wrap justify-end gap-2"
          aria-label="主要ナビゲーション"
        >
          <Link
            href="/requests/"
            className="rounded-lg border px-4 py-2 text-sm hover:bg-gray-50"
          >
            📋 申請一覧
          </Link>
          <Link
            href="/evaluations/"
            className="rounded-lg border px-4 py-2 text-sm hover:bg-gray-50"
          >
            ⚖️ 比較評価
          </Link>
        </nav>
      </header>
      <ChatWindow />
    </main>
  );
}
