"use client";

import { ChatWindow } from "@/components/ChatWindow";
import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-bold">🛫 AI 出張申請エージェント</h1>
          <p className="text-sm text-gray-500">
            出張リクエストを入力してください
          </p>
        </div>
        <Link
          href="/requests/"
          className="rounded-lg border px-4 py-2 text-sm hover:bg-gray-50"
        >
          📋 申請一覧
        </Link>
      </header>
      <ChatWindow />
    </main>
  );
}
