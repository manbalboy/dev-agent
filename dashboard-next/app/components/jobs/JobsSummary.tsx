import { useMemo } from 'react';

/**
 * 작업 상태 집계를 카드 형태로 표시한다.
 * summary 키가 일부 없어도 0으로 안전하게 처리한다.
 */
export function JobsSummary({ summary }: { summary: Record<string, number> }) {
  const cards = useMemo(
    () => [
      ['전체', summary.total ?? 0],
      ['대기', summary.queued ?? 0],
      ['실행', summary.running ?? 0],
      ['완료', summary.done ?? 0],
      ['실패', summary.failed ?? 0],
    ],
    [summary],
  );

  return (
    <div className="grid5">
      {cards.map(([k, v]) => (
        <article key={k} className="card">
          <p className="k">{k}</p>
          <p className="v">{v}</p>
        </article>
      ))}
    </div>
  );
}
