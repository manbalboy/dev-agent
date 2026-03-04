'use client';

import { useEffect, useState } from 'react';
import { Header } from './components/dashboard/Header';
import { Tabs, type DashboardTab } from './components/dashboard/Tabs';
import { JobsSummary } from './components/jobs/JobsSummary';
import { JobsTable } from './components/jobs/JobsTable';
import { AppManagementCard } from './components/settings/AppManagementCard';
import { IssueRegistrationCard } from './components/settings/IssueRegistrationCard';
import { WorkflowCatalogCard } from './components/settings/WorkflowCatalogCard';
import { WorkflowNodeBuilderCard } from './components/settings/WorkflowNodeBuilderCard';
import { api } from './lib/api';
import type { AppItem, JobsResponse, WorkflowDefinition, WorkflowsResponse } from './types/dashboard';

type RawJob = Record<string, unknown>;

function asText(value: unknown, fallback = '-'): string {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed || fallback;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function normalizeJob(raw: RawJob) {
  const status = asText(raw.status ?? raw.state, 'queued');
  const stage = asText(raw.stage ?? raw.phase ?? raw.current_stage, status);
  return {
    job_id: asText(raw.job_id ?? raw.id, ''),
    issue_number: asNumber(raw.issue_number),
    issue_title: asText(raw.issue_title, '-'),
    issue_url: asText(raw.issue_url, '#'),
    status,
    stage,
    pr_url: raw.pr_url == null ? null : asText(raw.pr_url, ''),
    updated_at: asText(raw.updated_at, ''),
    app_code: asText(raw.app_code, 'default'),
    track: asText(raw.track, 'new'),
  };
}

function isWorkflowsResponse(value: unknown): value is WorkflowsResponse {
  if (!value || typeof value !== 'object') return false;
  const obj = value as Record<string, unknown>;
  if (typeof obj.default_workflow_id !== 'string') return false;
  if (!Array.isArray(obj.workflows)) return false;
  return true;
}

export default function Page() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [tab, setTab] = useState<DashboardTab>('jobs');
  const [jobs, setJobs] = useState<JobsResponse['jobs']>([]);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [jobsMsg, setJobsMsg] = useState('');

  const [apps, setApps] = useState<AppItem[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowsResponse | null>(null);
  const [workflowMsg, setWorkflowMsg] = useState('');
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowDefinition | null>(null);

  const [issueApp, setIssueApp] = useState('default');
  const [issueTrack, setIssueTrack] = useState('new');
  const [issueTitle, setIssueTitle] = useState('');
  const [issueBody, setIssueBody] = useState('');
  const [issueMsg, setIssueMsg] = useState('');

  const [appCode, setAppCode] = useState('');
  const [appName, setAppName] = useState('');
  const [appMsg, setAppMsg] = useState('');

  /**
   * 실시간 작업 모니터링을 위해 3초마다 호출되는 핵심 로더.
   */
  async function loadJobs() {
    try {
      const r = await fetch(api('/api/jobs'), { cache: 'no-store' });
      if (!r.ok) {
        setJobsMsg(`작업 조회 실패 (${r.status})`);
        return;
      }
      const data = await r.json() as { jobs?: unknown; summary?: unknown };
      const rows = Array.isArray(data.jobs)
        ? data.jobs
            .filter((item): item is RawJob => typeof item === 'object' && item !== null)
            .map(normalizeJob)
            .filter((item) => item.job_id.length > 0)
        : [];
      setJobs(rows);
      setSummary((data.summary as Record<string, number>) ?? {});
      setJobsMsg('');
    } catch {
      setJobsMsg('작업 조회 중 네트워크 오류');
    }
  }

  /**
   * 앱 선택 드롭다운, 앱 관리 테이블의 공통 데이터 소스.
   */
  async function loadApps() {
    try {
      const r = await fetch(api('/api/apps'), { cache: 'no-store' });
      if (!r.ok) {
        setAppMsg(`앱 목록 조회 실패 (${r.status})`);
        return;
      }
      const data = await r.json();
      setApps(data.apps ?? []);
      if ((data.apps ?? []).length > 0 && !data.apps.find((a: AppItem) => a.code === issueApp)) {
        setIssueApp(data.apps[0].code);
      }
      setAppMsg('');
    } catch {
      setAppMsg('앱 목록 조회 중 네트워크 오류');
    }
  }

  /**
   * 서버 워크플로우 카탈로그를 불러와 조회 테이블에 반영한다.
   */
  async function loadWorkflows() {
    try {
      const r = await fetch(api('/api/workflows'), { cache: 'no-store' });
      if (!r.ok) {
        setWorkflowMsg(`워크플로우 조회 실패 (${r.status})`);
        return;
      }
      const payload: unknown = await r.json();
      if (!isWorkflowsResponse(payload)) {
        setWorkflowMsg('워크플로우 응답 형식이 올바르지 않습니다.');
        return;
      }
      setWorkflows(payload);
      setWorkflowMsg('');
    } catch {
      setWorkflowMsg('워크플로우 조회 중 네트워크 오류');
    }
  }

  useEffect(() => {
    const stored = typeof window !== 'undefined' ? window.localStorage.getItem('agenthub-theme') : null;
    const resolved = stored === 'light' ? 'light' : 'dark';
    setTheme(resolved);
    document.documentElement.setAttribute('data-theme', resolved);
  }, []);

  useEffect(() => {
    loadJobs();
    loadApps();
    loadWorkflows();
    const id = setInterval(loadJobs, 3000);
    return () => clearInterval(id);
  }, []);

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    document.documentElement.setAttribute('data-theme', next);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('agenthub-theme', next);
    }
  }

  /**
   * 이슈 등록 시 백엔드에서 job 생성 및 트리거링이 이어지므로,
   * 성공 후 입력값을 비우고 목록을 재조회해 화면과 실제 상태를 맞춘다.
   */
  async function registerIssue() {
    setIssueMsg('등록 중...');
    const r = await fetch(api('/api/issues/register'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        app_code: issueApp,
        track: issueTrack,
        title: issueTitle,
        body: issueBody,
      }),
    });
    const payload = await r.json();
    if (!r.ok) {
      setIssueMsg(payload.detail ?? '등록 실패');
      return;
    }
    setIssueMsg(`등록 완료: 이슈 #${payload.issue_number}, 작업 ${String(payload.job_id).slice(0, 8)}`);
    setIssueTitle('');
    setIssueBody('');
    loadJobs();
  }

  async function saveApp() {
    setAppMsg('저장 중...');
    const r = await fetch(api('/api/apps'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: appCode, name: appName }),
    });
    const payload = await r.json();
    if (!r.ok) {
      setAppMsg(payload.detail ?? '저장 실패');
      return;
    }
    setAppMsg(`저장 완료: ${appCode}`);
    setAppCode('');
    setAppName('');
    loadApps();
  }

  async function removeApp(code: string) {
    const r = await fetch(api(`/api/apps/${encodeURIComponent(code)}`), { method: 'DELETE' });
    const payload = await r.json();
    if (!r.ok) {
      setAppMsg(payload.detail ?? '삭제 실패');
      return;
    }
    setAppMsg(`삭제 완료: ${code}`);
    loadApps();
  }

  /**
   * 앱별 workflow_id 매핑 저장.
   * 향후 워커가 app_code 기준으로 실행 플로우를 선택할 때 사용한다.
   */
  async function mapAppWorkflow(code: string, workflowId: string) {
    setAppMsg('워크플로우 매핑 저장 중...');
    const r = await fetch(api(`/api/apps/${encodeURIComponent(code)}/workflow`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workflow_id: workflowId }),
    });
    const payload = await r.json();
    if (!r.ok) {
      setAppMsg(payload.detail ?? '워크플로우 매핑 저장 실패');
      return;
    }
    setAppMsg(`매핑 저장 완료: ${code} -> ${workflowId}`);
    loadApps();
  }

  /**
   * 설정 탭 워크플로우 행 더블클릭 시 호출된다.
   * 노드 편집 탭으로 전환하고 해당 플로우를 편집기에 로드한다.
   */
  function openWorkflowInEditor(workflow: WorkflowDefinition) {
    setSelectedWorkflow(workflow);
    setTab('workflow');
  }

  return (
    <main>
      <Header
        title="AgentHub Dashboard (Next)"
        subtitle="React/Next 기반 대시보드 프로토타입"
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <Tabs tab={tab} onChange={setTab} />

      {tab === 'jobs' && (
        <section>
          {jobsMsg ? <p className="hint">{jobsMsg}</p> : null}
          <JobsSummary summary={summary} />
          <JobsTable jobs={jobs} />
        </section>
      )}

      {tab === 'settings' && (
        <section className="pane">
          <IssueRegistrationCard
            apps={apps}
            issueApp={issueApp}
            issueTrack={issueTrack}
            issueTitle={issueTitle}
            issueBody={issueBody}
            issueMsg={issueMsg}
            setIssueApp={setIssueApp}
            setIssueTrack={setIssueTrack}
            setIssueTitle={setIssueTitle}
            setIssueBody={setIssueBody}
            onRegister={registerIssue}
          />

          <AppManagementCard
            apps={apps}
            workflowOptions={(workflows?.workflows ?? []).map((workflow) => ({
              workflow_id: workflow.workflow_id,
              name: workflow.name,
            }))}
            appCode={appCode}
            appName={appName}
            appMsg={appMsg}
            setAppCode={setAppCode}
            setAppName={setAppName}
            onSave={saveApp}
            onRemove={removeApp}
            onMapWorkflow={mapAppWorkflow}
          />

          <WorkflowCatalogCard workflows={workflows} workflowMsg={workflowMsg} onOpenWorkflow={openWorkflowInEditor} />
        </section>
      )}

      {tab === 'workflow' && (
        <section className="pane">
          <WorkflowNodeBuilderCard initialWorkflow={selectedWorkflow} />
        </section>
      )}
    </main>
  );
}
