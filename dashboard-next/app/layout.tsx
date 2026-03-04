import './globals.css';
import '@xyflow/react/dist/style.css';
import type { Metadata } from 'next';
import type { ReactNode } from 'react';

export const metadata: Metadata = {
  title: 'AgentHub Dashboard (Next)',
  description: 'React/Next 기반 AgentHub 대시보드',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
