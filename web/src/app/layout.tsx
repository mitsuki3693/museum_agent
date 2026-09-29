import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "馆语 · 博物馆可信问答",
  description: "基于公开馆藏资料的可溯源问答演示",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
