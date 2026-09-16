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
    <html lang="ja" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(() => {
  const param = new URLSearchParams(window.location.search).get("scoutTheme");
  const theme =
    param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
})();`,
          }}
        />
      </head>
      <body className="app-body">{children}</body>
    </html>
  );
}
