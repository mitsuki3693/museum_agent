import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MUSE · 你的博物馆随行助手",
  description: "拍照、听讲解、找路线。基于馆藏与馆方资料的博物馆随行助手演示。",
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
