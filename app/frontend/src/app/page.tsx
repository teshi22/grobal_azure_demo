"use client";

import { ChatWindow } from "@/components/ChatWindow";

export default function Home() {
  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col">
      <header className="border-b px-6 py-4">
        <h1 className="text-xl font-bold">🛫 AI 出張申請エージェント</h1>
        <p className="text-sm text-gray-500">
          出張リクエストを入力してください
        </p>
      </header>
      <ChatWindow />
    </main>
  );
}
