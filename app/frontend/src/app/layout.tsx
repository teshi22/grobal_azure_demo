import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 出張申請エージェント",
  description: "AI を活用した出張申請ワークフロー",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body className="bg-gray-50 text-gray-900 antialiased">{children}</body>
    </html>
  );
}
