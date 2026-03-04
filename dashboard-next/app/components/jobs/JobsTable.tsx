import { kst } from '../../lib/time';
import type { Job } from '../../types/dashboard';

/**
 * 작업 목록 테이블.
 * 최신 갱신 시각은 한국 시간으로 변환해 보여준다.
 */
export function JobsTable({ jobs }: { jobs: Job[] }) {
  function statusLabel(status: string): string {
    if (status === 'queued') return '대기';
    if (status === 'running') return '실행';
    if (status === 'done') return '완료';
    if (status === 'failed') return '실패';
    return status || '-';
  }

  function statusClass(status: string): string {
    if (status === 'queued') return 'status queued';
    if (status === 'running') return 'status running';
    if (status === 'done') return 'status done';
    if (status === 'failed') return 'status failed';
    return 'status';
  }

  function stageLabel(stage: string): string {
    if (stage === 'done') return '완료';
    if (stage === 'failed') return '실패';
    if (stage === 'queued') return '대기';
    return stage || '-';
  }

  function stageRole(stage: string): string {
    if (!stage) return '-';
    if (stage === 'done' || stage === 'failed') return '-';
    if (stage.includes('plan')) return '플래너';
    if (stage.includes('implement') || stage.includes('fix')) return '코더';
    if (stage.includes('test')) return '테스터';
    if (stage.includes('review')) return '리뷰어';
    if (stage.includes('escalat')) return '중재자';
    return '-';
  }

  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>
            <th>작업 ID</th>
            <th>앱/트랙</th>
            <th>상태</th>
            <th>단계</th>
            <th>이슈</th>
            <th>PR</th>
            <th>갱신(KST)</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.job_id}>
              <td><a href={`/jobs/${encodeURIComponent(job.job_id)}`}>{job.job_id.slice(0, 8)}</a></td>
              <td>
                <span className="badge">{job.app_code ?? 'default'}</span>{' '}
                <span className="badge">{job.track ?? 'new'}</span>
              </td>
              <td>
                <span className={statusClass(job.status)}>{statusLabel(job.status)}</span>
              </td>
              <td>
                <span>{stageLabel(job.stage)}</span>{' '}
                <span className="badge">{stageRole(job.stage)}</span>
              </td>
              <td>
                <a href={job.issue_url} target="_blank" rel="noreferrer">#{job.issue_number}</a>
              </td>
              <td>{job.pr_url ? <a href={job.pr_url} target="_blank" rel="noreferrer">PR</a> : '-'}</td>
              <td>{kst(job.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
