"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchTravelRequests } from "@/lib/api";
import type { TravelRequest } from "@/lib/types";
import { TravelRequestCard } from "@/components/TravelRequestCard";

export default function RequestsPage() {
  const [requests, setRequests] = useState<TravelRequest[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadRequests = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchTravelRequests();
      setRequests(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "取得に失敗しました");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadRequests();
  }, []);

  return (
    <main className="mx-auto flex h-screen max-w-4xl flex-col">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-bold">📋 出張申請一覧</h1>
          <p className="text-sm text-gray-500">
            登録済みの出張申請を確認できます
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={loadRequests}
            disabled={isLoading}
            className="rounded-lg border px-4 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            🔄 更新
          </button>
          <Link
            href="/"
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
          >
            🛫 申請する
          </Link>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <div className="h-3 w-3 animate-pulse rounded-full bg-blue-400" />
            <span className="ml-2 text-sm text-gray-400">読み込み中...</span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            ❌ {error}
          </div>
        )}

        {!isLoading && !error && requests.length === 0 && (
          <div className="py-12 text-center text-gray-400">
            <p className="text-4xl">📭</p>
            <p className="mt-2">申請データがありません</p>
            <Link
              href="/"
              className="mt-4 inline-block rounded-lg bg-blue-600 px-6 py-2 text-sm text-white hover:bg-blue-700"
            >
              出張申請を作成する
            </Link>
          </div>
        )}

        {!isLoading && requests.length > 0 && (
          <div className="grid gap-4 md:grid-cols-2">
            {requests.map((req) => (
              <TravelRequestCard key={req.request_id} request={req} />
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
