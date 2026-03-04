/**
 * 대시보드에서 사용하는 작업(Job) 단위 데이터 모델.
 * 백엔드 /api/jobs 응답의 각 항목을 그대로 반영한다.
 */
export type Job = {
  job_id: string;
  issue_number: number;
  issue_title: string;
  issue_url: string;
  status: string;
  stage: string;
  pr_url?: string | null;
  updated_at: string;
  app_code?: string;
  track?: string;
};

/**
 * /api/jobs 응답 전체 구조.
 * jobs 목록과 상태별 집계를 함께 내려주므로 화면 카드와 테이블을 동시에 구성할 수 있다.
 */
export type JobsResponse = {
  jobs: Job[];
  summary: Record<string, number>;
};

/**
 * 앱 관리 테이블에서 사용하는 앱 정보.
 */
export type AppItem = {
  code: string;
  name: string;
  repository: string;
  workflow_id?: string;
};

/**
 * /api/workflows 응답 구조.
 */
export type WorkflowsResponse = {
  default_workflow_id: string;
  workflows: WorkflowDefinition[];
};

export type WorkflowDefinition = {
  workflow_id: string;
  name: string;
  version: number;
  description?: string;
  entry_node_id?: string;
  nodes?: Array<{ id: string; type: string; title: string }>;
  edges?: Array<{ from: string; to: string; on?: string }>;
};

/**
 * 노드 편집기(보일러플레이트 이식)에서 사용할 노드 타입 정의.
 * 외부 Workflow 저장소의 SUPPORTED_NODE_TYPES를 동일한 의미로 가져왔다.
 */
export const SUPPORTED_NODE_TYPES = [
  { type: 'gh_read_issue', title: '이슈 읽기', category: 'GitHub', color: '#8B5CF6' },
  { type: 'if_label_match', title: '라벨 IF 분기', category: 'Control', color: '#0EA5E9' },
  { type: 'loop_until_pass', title: '루프 노드', category: 'Control', color: '#06B6D4' },
  { type: 'write_spec', title: '스펙 작성', category: 'Documentation', color: '#3B82F6' },
  { type: 'gemini_plan', title: 'Gemini 계획', category: 'AI Planning', color: '#10B981' },
  { type: 'designer_task', title: '디자이너(Codex)', category: 'AI Design', color: '#111827' },
  { type: 'codex_implement', title: 'Codex 구현', category: 'AI Coding', color: '#F59E0B' },
  { type: 'test_after_implement', title: '구현 후 테스트', category: 'Testing', color: '#EF4444' },
  { type: 'commit_implement', title: '구현 커밋', category: 'Git', color: '#EC4899' },
  { type: 'gemini_review', title: 'Gemini 리뷰', category: 'AI Review', color: '#10B981' },
  { type: 'codex_fix', title: 'Codex 수정', category: 'AI Coding', color: '#F59E0B' },
  { type: 'test_after_fix', title: '수정 후 테스트', category: 'Testing', color: '#EF4444' },
  { type: 'commit_fix', title: '수정 커밋', category: 'Git', color: '#EC4899' },
  { type: 'push_branch', title: '브랜치 푸시', category: 'Git', color: '#EC4899' },
  { type: 'create_pr', title: 'PR 생성', category: 'GitHub', color: '#8B5CF6' },
] as const;

export type SupportedNodeType = (typeof SUPPORTED_NODE_TYPES)[number];

/**
 * 실제 편집 중인 노드 인스턴스.
 * params_text는 사용자가 JSON 문자열로 직접 입력하는 영역이다.
 */
export type WorkflowNodeInput = {
  id: string;
  type: string;
  title: string;
  category: string;
  color: string;
  params_text: string;
};
