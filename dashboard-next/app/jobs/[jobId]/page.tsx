'use client';

import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

type JobDetail = {
  job_id: string;
  status: string;
  stage: string;
  app_code?: string;
  track?: string;
  attempt: number;
  max_attempts: number;
  repository: string;
  issue_url: string;
  issue_number: number;
  issue_title: string;
  branch_name: string;
  pr_url?: string | null;
  error_message?: string | null;
  log_file: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

type MDFile = {
  name: string;
  content: string;
};

type ApiResponse = {
  job?: JobDetail;
  md_files?: MDFile[];
  stop_requested?: boolean;
};

type LogEntry = {
  timestamp: string;
  message: string;
  actor: string;
  raw: string;
  attempt?: string;
  stage?: string;
};

function esc(value: string): string {
  let text = value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  // Console Highlighting using RegEx
  text = text.replace(/\[RUN\]/g, '<span class="hl-run">[RUN]</span>');
  text = text.replace(/\[STDOUT\]/g, '<span class="hl-stdout">[STDOUT]</span>');
  text = text.replace(/\[STDERR\]/g, '<span class="hl-stderr">[STDERR]</span>');
  text = text.replace(/\[DONE\]/g, '<span class="hl-done">[DONE]</span>');
  
  // Highlighting URLs
  text = text.replace(/(https?:\/\/[^\s]+)/g, '<span class="hl-url">$1</span>');
  
  // Highlighting Paths (common in agentHub)
  text = text.replace(/(\/home\/docker\/[a-zA-Z0-9._/-]+)/g, '<span class="hl-path">$1</span>');

  // Keywords - Success
  text = text.replace(/\b(success|succeeded|done|passed|ok)\b/gi, '<span class="hl-keyword-success">$1</span>');
  
  // Keywords - Fail
  text = text.replace(/\b(fail|failed|failure|error|exception|rejected)\b/gi, '<span class="hl-keyword-fail">$1</span>');

  return text;
}

function formatKstDateTime(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  const text = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed);
  return `${text} KST`;
}

function parseActorTaggedMessage(message: string): { actor: string | null; message: string } {
  const matched = String(message || '').match(/^\[ACTOR:([A-Z_]+)\]\s*(.*)$/);
  if (!matched) {
    return { actor: null, message: String(message || '') };
  }
  return {
    actor: matched[1] ?? null,
    message: matched[2] ?? '',
  };
}

function inferActorFromRunCommand(command: string): string {
  const lowered = String(command || '').toLowerCase();
  if ((lowered.includes('plann') || lowered.includes('planner')) && lowered.includes('gemini')) return 'PLANNER';
  if ((lowered.includes('review') || lowered.includes('reviewer')) && lowered.includes('gemini')) return 'REVIEWER';
  if (lowered.includes('codex')) return 'CODER';
  if (lowered.startsWith('gh ') || lowered.includes(' gh ')) return 'GITHUB';
  if (lowered.startsWith('git ') || lowered.includes(' git ')) return 'GIT';
  if (
    lowered.includes('systemctl') ||
    lowered.includes('service ') ||
    lowered.includes('journalctl') ||
    lowered.includes('docker ') ||
    lowered.includes('kubectl ')
  ) {
    return 'SYSTEM';
  }
  if (
    lowered.includes('bash -lc') ||
    lowered.includes('sh -c') ||
    lowered.startsWith('python ') ||
    lowered.startsWith('python3 ')
  ) {
    return 'SHELL';
  }
  return 'ORCHESTRATOR';
}

function inferActorFromMessage(message: string, activeActor: string): string {
  const msg = String(message || '');
  if (msg.startsWith('[RUN] ')) {
    const command = msg.replace('[RUN] ', '');
    return inferActorFromRunCommand(command);
  }
  if (msg.startsWith('[STDOUT]') || msg.startsWith('[STDERR]') || msg.startsWith('[DONE]')) {
    return activeActor || 'ORCHESTRATOR';
  }
  if (msg.startsWith('[STAGE] ') || msg.startsWith('Attempt ') || msg.startsWith('Starting job ')) {
    return 'ORCHESTRATOR';
  }
  return activeActor || 'ORCHESTRATOR';
}

function parseLogEntries(logText: string): LogEntry[] {
  const lines = (logText || '').split('\n');
  const entries: LogEntry[] = [];
  let current: LogEntry | null = null;
  let activeActor = 'ORCHESTRATOR';
  const tsLine = /^\[([^\]]+)\]\s(.*)$/;

  for (const line of lines) {
    const matched = line.match(tsLine);
    if (matched) {
      if (current) entries.push(current);
      const message = matched[2] || '';
      const parsed = parseActorTaggedMessage(message);
      const inferred = parsed.actor || inferActorFromMessage(parsed.message, activeActor);
      if (parsed.message.startsWith('[RUN] ')) {
        activeActor = inferred;
      } else if (
        parsed.message.startsWith('[STDOUT]') ||
        parsed.message.startsWith('[STDERR]') ||
        parsed.message.startsWith('[DONE]')
      ) {
        activeActor = inferred;
      }
      const ts = matched[1] || '';
      current = {
        timestamp: ts,
        message: parsed.message,
        actor: inferred,
        raw: `[${ts}] ${parsed.message}`,
      };
    } else if (current) {
      current.raw += `\n${line}`;
    } else if (line.trim()) {
      current = {
        timestamp: '',
        message: line,
        actor: 'ORCHESTRATOR',
        raw: line,
      };
    }
  }

  if (current) entries.push(current);
  return entries;
}

function classifyLogEntry(entry: LogEntry): 'attempt' | 'stage' | 'run' | 'done' | 'warn' | 'error' | 'info' {
  const msg = String(entry.message || '');
  if (msg.startsWith('Attempt ') && msg.includes(' failed:')) return 'error';
  if (msg.includes('Maximum retry count reached')) return 'error';
  if (msg.startsWith('Attempt ')) return 'attempt';
  if (msg.startsWith('[STAGE] ')) return 'stage';
  if (msg.startsWith('[RUN] ')) return 'run';
  if (msg.startsWith('[DONE] ')) {
    const matched = msg.match(/exit_code=(\d+)/);
    if (matched && Number(matched[1]) !== 0) return 'error';
    return 'done';
  }
  if (msg.startsWith('[STDERR]')) return 'warn';
  return 'info';
}

function displayLabel(type: ReturnType<typeof classifyLogEntry>): string {
  if (type === 'attempt') return 'ATTEMPT';
  if (type === 'stage') return 'STAGE';
  if (type === 'run') return 'RUN';
  if (type === 'done') return 'DONE';
  if (type === 'warn') return 'WARN';
  if (type === 'error') return 'ERROR';
  return 'INFO';
}

type LogGroup = {
  key: string;
  attempt: string;
  stage: string;
  entries: LogEntry[];
};

function buildGroupedEntries(entriesNewestFirst: LogEntry[]): LogGroup[] {
  const chronological = [...entriesNewestFirst].reverse();
  let currentAttempt = 'attempt:0';
  let currentStage = 'stage:queued';

  for (const entry of chronological) {
    const attemptMatch = String(entry.message || '').match(/^Attempt\s+(\d+)/);
    if (attemptMatch) currentAttempt = `attempt:${attemptMatch[1]}`;
    const stageMatch = String(entry.message || '').match(/^\[STAGE\]\s+([a-zA-Z0-9_:-]+)/);
    if (stageMatch) currentStage = `stage:${stageMatch[1]}`;
    entry.attempt = currentAttempt;
    entry.stage = currentStage;
  }

  const grouped: LogGroup[] = [];
  const map = new Map<string, LogGroup>();
  for (const entry of entriesNewestFirst) {
    const key = `${entry.attempt}|${entry.stage}`;
    if (!map.has(key)) {
      const group: LogGroup = {
        key,
        attempt: entry.attempt ?? 'attempt:0',
        stage: entry.stage ?? 'stage:queued',
        entries: [],
      };
      map.set(key, group);
      grouped.push(group);
    }
    map.get(key)?.entries.push(entry);
  }
  return grouped;
}

function summarizeGroup(group: LogGroup): string {
  const latest = group.entries[0];
  const stage = String(latest.stage || 'unknown').replace(/^stage:/, '');
  const attempt = String(latest.attempt || '1').replace(/^attempt:/, '');
  return `시도 ${attempt} · ${stage} · ${group.entries.length}줄`;
}

export default function JobDetailPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = decodeURIComponent(params.jobId);

  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [job, setJob] = useState<JobDetail | null>(null);
  const [mdFiles, setMdFiles] = useState<MDFile[]>([]);
  const [stopRequested, setStopRequested] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [terminalUpdatedAt, setTerminalUpdatedAt] = useState('-');
  const [logText, setLogText] = useState('로그를 불러오는 중...');
  const [errorMessage, setErrorMessage] = useState('');
  const terminalRef = useRef<HTMLDivElement | null>(null);
  const shouldStickTopRef = useRef(true);
  const lastLogTextRef = useRef('');

  const entriesNewestFirst = useMemo(() => parseLogEntries(logText).reverse(), [logText]);
  const groups = useMemo(() => buildGroupedEntries(entriesNewestFirst), [entriesNewestFirst]);
  const highlights = useMemo(
    () =>
      entriesNewestFirst
        .map((entry) => ({ ...entry, type: classifyLogEntry(entry) }))
        .filter((entry) => entry.type === 'error' || entry.type === 'warn')
        .slice(0, 3),
    [entriesNewestFirst],
  );

  useEffect(() => {
    const stored = typeof window !== 'undefined' ? window.localStorage.getItem('agenthub-theme') : null;
    const resolved = stored === 'light' ? 'light' : 'dark';
    setTheme(resolved);
    document.documentElement.setAttribute('data-theme', resolved);
  }, []);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    if (shouldStickTopRef.current) {
      terminal.scrollTop = 0;
    }
  }, [groups]);

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    document.documentElement.setAttribute('data-theme', next);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('agenthub-theme', next);
    }
  };

  const isNearBottom = (element: HTMLDivElement): boolean => {
    const threshold = 40;
    return element.scrollHeight - element.scrollTop - element.clientHeight < threshold;
  };

  const isNearTop = (element: HTMLDivElement): boolean => element.scrollTop < 40;

  const refreshJob = useCallback(async (): Promise<JobDetail | null> => {
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
      if (response.status === 404) {
        setErrorMessage('작업을 찾을 수 없습니다.');
        return null;
      }
      if (!response.ok) return null;
      const payload = await response.json() as ApiResponse;
      if (!payload.job) return null;
      
      setJob(payload.job);
      if (payload.md_files) {
        setMdFiles(payload.md_files);
      }
      setStopRequested(!!payload.stop_requested);
      setTerminalUpdatedAt(formatKstDateTime(new Date().toISOString()));
      setErrorMessage('');
      return payload.job;
    } catch {
      setErrorMessage('작업 상세를 불러오는 중 네트워크 오류가 발생했습니다.');
      return null;
    }
  }, [jobId]);

  const refreshLog = useCallback(async (logFile: string): Promise<void> => {
    try {
      const response = await fetch(`/logs/${encodeURIComponent(logFile)}`, { cache: 'no-store' });
      if (response.status === 404) {
        setLogText('[로그 파일이 아직 생성되지 않았습니다]');
        return;
      }
      if (!response.ok) {
        setLogText('[로그를 읽는 중 오류가 발생했습니다]');
        return;
      }

      const text = await response.text();
      if (text === lastLogTextRef.current) return;
      lastLogTextRef.current = text;

      const terminal = terminalRef.current;
      if (terminal) {
        shouldStickTopRef.current = isNearTop(terminal) || isNearBottom(terminal);
      } else {
        shouldStickTopRef.current = true;
      }

      setLogText(text.trim() ? text : '[로그 대기 중]');
    } catch {
      setLogText('[네트워크 오류로 로그를 읽지 못했습니다]');
    }
  }, []);

  const requestStop = async () => {
    if (isStopping) return;
    setIsStopping(true);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const payload = await response.json() as { requested: boolean; detail?: string };
      if (!response.ok) {
        alert(payload.detail || '정지 요청 실패');
        return;
      }
      setStopRequested(true);
    } catch {
      alert('네트워크 오류로 정지 요청에 실패했습니다.');
    } finally {
      setIsStopping(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    const refreshAll = async () => {
      if (!mounted) return;
      const latestJob = (await refreshJob()) ?? job;
      if (latestJob?.log_file) {
        await refreshLog(latestJob.log_file);
      }
    };

    refreshAll();
    const id = window.setInterval(refreshAll, 1500);

    return () => {
      mounted = false;
      window.clearInterval(id);
    };
  }, [refreshJob, refreshLog, job]);

  return (
    <main className="jobd-container">
      <header className="jobd-page-head">
        <p className="jobd-back-link"><a href="/">← 목록으로</a></p>
        <div className="jobd-page-head-row">
          <h1>작업 상세 · {jobId}</h1>
          <div className="row" style={{ alignItems: 'center', gap: '12px' }}>
            <button 
              className="btn btnDanger" 
              type="button" 
              onClick={requestStop} 
              disabled={isStopping || stopRequested || !job || !(job.status === 'queued' || job.status === 'running')}
            >
              {isStopping ? '요청 중...' : stopRequested ? '정지 요청됨' : '정지 요청(라운드 종료 후)'}
            </button>
            <button className="btn themeToggle" type="button" onClick={toggleTheme}>
              <span className={`themeMark ${theme === 'dark' ? 'active' : ''}`}>☾</span>
              <span className={`themeMark ${theme === 'light' ? 'active' : ''}`}>☀</span>
            </button>
          </div>
        </div>
        {stopRequested && (
          <p className="hint" style={{ color: '#ffb4b4', fontWeight: 'bold' }}>
            정지 요청됨: 현재 라운드 완료 후 작업이 종료됩니다.
          </p>
        )}
      </header>

      {errorMessage ? <p className="hint">{errorMessage}</p> : null}

      <dl className="jobd-meta-grid">
        <dt>상태</dt><dd>{job?.status ?? '-'}</dd>
        <dt>단계</dt><dd>{job?.stage ?? '-'}</dd>
        <dt>앱</dt><dd>{job?.app_code || 'default'}</dd>
        <dt>트랙</dt><dd>{job?.track || 'new'}</dd>
        <dt>시도 횟수</dt><dd>{job ? `${job.attempt} / ${job.max_attempts}` : '-'}</dd>
        <dt>저장소</dt><dd>{job?.repository ?? '-'}</dd>
        <dt>이슈</dt>
        <dd>
          {job ? (
            <a href={job.issue_url} target="_blank" rel="noreferrer">#{job.issue_number} - {job.issue_title}</a>
          ) : '-'}
        </dd>
        <dt>브랜치</dt><dd>{job?.branch_name ?? '-'}</dd>
        <dt>PR</dt>
        <dd>
          {job?.pr_url ? <a href={job.pr_url} target="_blank" rel="noreferrer">{job.pr_url}</a> : '아직 없음'}
        </dd>
        <dt>오류</dt><dd>{job?.error_message || '-'}</dd>
        <dt>로그</dt>
        <dd>
          {job?.log_file ? <a href={`/logs/${encodeURIComponent(job.log_file)}`} target="_blank" rel="noreferrer">{job.log_file}</a> : '-'}
        </dd>
        <dt>생성</dt><dd>{formatKstDateTime(job?.created_at)}</dd>
        <dt>시작</dt><dd>{formatKstDateTime(job?.started_at ?? null)}</dd>
        <dt>종료</dt><dd>{formatKstDateTime(job?.finished_at ?? null)}</dd>
      </dl>

      {mdFiles.length > 0 && (
        <section className="jobd-panel" style={{ marginBottom: '1.5rem' }}>
          <div className="jobd-panel-head">
            <h2>에이전트 산출물 (.md)</h2>
            <p className="hint">각 에이전트가 생성한 설계 및 계획 문서입니다.</p>
          </div>
          <div style={{ padding: '0 1rem 1rem' }}>
            {mdFiles.map((file) => (
              <details key={file.name} style={{ marginBottom: '0.75rem', border: '1px solid var(--border-color)', borderRadius: '6px' }}>
                <summary style={{ padding: '0.75rem', cursor: 'pointer', fontWeight: 'bold', backgroundColor: 'var(--bg-card)' }}>
                  📄 {file.name}
                </summary>
                <div style={{ padding: '1rem', backgroundColor: 'var(--bg-page)', borderTop: '1px solid var(--border-color)', overflowX: 'auto' }}>
                  <div className="markdown-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {file.content}
                    </ReactMarkdown>
                  </div>
                </div>
              </details>
            ))}
          </div>
        </section>
      )}

      <section className="jobd-panel">
        <div className="jobd-panel-head">
          <h2>실시간 터미널 로그</h2>
          <p className="hint">마지막 갱신: {terminalUpdatedAt}</p>
        </div>

        <div className="jobd-failure-summary">
          {highlights.length === 0 ? (
            <p className="jobd-failure-empty">최근 실패/경고 신호가 없습니다.</p>
          ) : (
            <>
              <h3>실패 요약</h3>
              <ul className="jobd-failure-list">
                {highlights.map((item, index) => {
                  const firstLine = String(item.message || '').split('\n')[0];
                  return (
                    <li key={`${item.timestamp}-${index}`} className={`jobd-failure-item type-${item.type}`}>
                      <span className="jobd-failure-type">{displayLabel(item.type)}</span>
                      <span className="jobd-failure-actor">{item.actor || 'ORCHESTRATOR'}</span>
                      <span className="jobd-failure-text">{firstLine}</span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>

        <div className="jobd-terminal-wrap">
          <div className="jobd-terminal-topbar">
            <span className="jobd-terminal-dot red" />
            <span className="jobd-terminal-dot yellow" />
            <span className="jobd-terminal-dot green" />
            <span className="jobd-terminal-title">agenthub / {job?.log_file ?? '-'}</span>
          </div>

          <div className="jobd-terminal-log" ref={terminalRef}>
            {groups.length === 0 ? (
              <p>[로그 대기 중]</p>
            ) : (
              groups.map((group, index) => (
                <details className="jobd-log-group" key={group.key + index} open={index < 2}>
                  <summary>{summarizeGroup(group)}</summary>
                  <div className="jobd-log-group-body">
                    {group.entries.map((entry, entryIndex) => {
                      const type = classifyLogEntry(entry);
                      return (
                        <article className={`jobd-log-entry type-${type}`} key={`${entry.timestamp}-${entryIndex}`}>
                          <div className="jobd-log-entry-head">
                            <span className="jobd-log-entry-tag">{displayLabel(type)}</span>
                            <span className={`jobd-log-entry-actor actor-${String(entry.actor || 'ORCHESTRATOR').toLowerCase()}`}>
                              {entry.actor || 'ORCHESTRATOR'}
                            </span>
                            <span className="jobd-log-entry-time">{formatKstDateTime(entry.timestamp || '')}</span>
                          </div>
                          <pre
                            className="jobd-log-entry-body"
                            dangerouslySetInnerHTML={{ __html: esc(entry.raw) }}
                          />
                        </article>
                      );
                    })}
                  </div>
                </details>
              ))
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
