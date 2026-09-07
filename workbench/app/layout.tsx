import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Zibuyu Studio · 制作与发布',
  description: 'Zibuyu TikTok 服装内容制作、成片验收与发布工作台',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
