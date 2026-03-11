# Future Direction Priority Roadmap

## 1. Purpose
- 이 문서는 `앞으로 더 추가해야 할 방향`을 제품 목표와 실제 구현 순서 관점에서 다시 정리한 우선순위 문서다.
- 기준 목표는 `알아서 성장하는 프로그램 개발자 동료`다.
- 전제는 바꾸지 않는다.
  - 한 번에 크게 바꾸지 않는다.
  - 항상 작은 슬라이스로 자른다.
  - shadow -> opt-in -> default 순서만 허용한다.
  - 테스트가 먼저이고, 전체 회귀를 마지막에 반드시 돈다.

## 2. Current Baseline
- Phase 1:
  - product pipeline / review / improvement spine 있음
- Phase 2:
  - adaptive memory artifact / retrieval / scoring / shadow 있음
- Phase 3:
  - memory DB / DB-first retrieval / operator memory tooling / backlog candidate queue 있음
- Phase 4:
  - internal tool catalog는 `research_search`, `log_lookup`, `repo_search`, `memory_search`까지 구현됨
  - MCP tool shadow는 trace-only 상태로 있음
  - vector DB는 shadow index + `memory_search` opt-in retrieval까지 들어감
  - LangGraph는 planner/recovery shadow trace 첫 슬라이스까지 들어감

## 3. North Star
- 이 시스템이 최종적으로 해야 하는 일은 아래다.
  - repo를 읽는다.
  - 현재 실패와 운영 상태를 이해한다.
  - memory를 semantic하게 재사용한다.
  - 필요한 tool을 안전하게 호출한다.
  - 선형 파이프라인이 아니라 상태형 graph/subgraph로 반복 조사한다.
  - 반복 실패와 기회 영역을 backlog로 만들고, 운영자 승인 아래 다음 작업으로 연결한다.

## 4. Priority Order

### Priority 1. Phase 4-D1 Vector Shadow Manifest
- Goal:
  - Qdrant read path를 열기 전에, 어떤 memory entry를 vector index 후보로 올릴지 먼저 고정한다.
- Small Slice:
  - feature flag `vector_memory_shadow`
  - `_docs/VECTOR_SHADOW_INDEX.json`
  - SQLite memory DB -> embedding payload selection
- Why first:
  - retrieval 결과를 바꾸지 않는다.
  - transport/Qdrant dependency 없이도 candidate quality를 검증할 수 있다.
  - operator가 shadow payload를 먼저 읽어볼 수 있다.
- Test:
  - unit: payload builder
  - regression: orchestrator retrieval artifact write path
  - off-path: feature flag false면 disabled artifact만 남아야 함

### Priority 2. Phase 4-D2 Qdrant Shadow Transport
- Goal:
  - manifest를 실제 Qdrant shadow collection upsert로 연결한다.
- Small Slice:
  - optional adapter
  - env-based config
  - shadow transport result trace
- Constraint:
  - retrieval primary path는 여전히 SQLite 유지

### Priority 3. Phase 4-E1 Vector Read Path For `memory_search`
- Goal:
  - 가장 좁은 surface인 `memory_search`에서만 vector retrieval opt-in 실험을 한다.
- Small Slice:
  - feature flag `vector_memory_retrieval`
  - metadata filter + vector similarity hybrid
  - `MEMORY_SEARCH_RESULT.json`에 source 표시
- Status:
  - `DONE`

### Priority 4. Phase 4-F1 Planner Subgraph Shadow
- Goal:
  - planner refinement loop만 LangGraph shadow subgraph로 분리한다.
- Small Slice:
  - draft/evaluate/refine loop shadow trace
  - 기존 `PLAN.md`, `PLAN_QUALITY.json` 계약 유지
- Status:
  - `DONE`

### Priority 5. Phase 4-G1 Recovery Subgraph Shadow
- Goal:
  - `run_tests -> analyze_failure -> fix_once -> retest`만 shadow subgraph로 분리한다.
- Small Slice:
  - recovery decision trace
  - 기존 retry policy 유지
- Status:
  - `DONE`

### Priority 6. Phase 3 Bridge Completion
- Goal:
  - approved backlog -> queued job bridge를 연결한다.
- Why still important:
  - 진짜 `self-growing companion`이 되려면 tool/vector/graph보다 결국 `다음 행동 연결`이 필요하다.
- Status:
  - `PARTIAL`
- Done:
  - candidate `approve / queue / dismiss`
  - follow-up job enqueue
  - `_docs/FOLLOWUP_BACKLOG_TASK.json`
  - planner prompt bridge
  - follow-up job contract 메타(`job_kind`, `parent_job_id`, `backlog_candidate_id`)
  - recurring failure pattern -> backlog candidate ingest
- Remaining:
  - backlog approval UX 보강

## 5. Non-Negotiable Rules

### 5.1 Small Slice Rule
- 한 슬라이스는 아래를 넘기지 않는다.
  - feature flag 1개
  - artifact/trace 1개
  - runtime adapter 1개
  - UI 변경 최대 1개

### 5.2 Test Rule
- 모든 슬라이스는 아래를 포함해야 한다.
  - unit test
  - existing contract regression test
  - feature-flag off-path test
  - full `PYTHONPATH=. .venv/bin/pytest -q`

### 5.3 Runtime Safety Rule
- primary retrieval / planner / recovery 결과를 한 번에 바꾸지 않는다.
- 먼저 trace만 쌓는다.
- shadow trace가 읽을 만해진 뒤에만 opt-in을 연다.

## 6. Immediate Next Deliverables
1. backlog approval UX 보강
2. follow-up job contract를 job detail/operator view에 더 명확히 노출
3. tool-using diagnosis loop의 첫 슬라이스 정의
4. 관련 회귀 테스트 추가
5. UI 품질 가드레일 + 운영자 입력 레지스트리

## 6.2 UI Quality / Operator Inputs
- 상세 계획은 [UI_QUALITY_AND_OPERATOR_INPUTS_PLAN.md](./UI_QUALITY_AND_OPERATOR_INPUTS_PLAN.md) 를 따른다.
- 목적:
  - UI 품질이 기능 추가 속도 때문에 계속 하락하지 않게 한다.
  - operator가 나중에 필요한 API key / tenant id / base URL 같은 값을 안전하게 제공할 수 있게 한다.
- 현재 상태:
  - `PARTIAL`
- Done:
  - planner/coder prompt에 mobile-first / complexity control / benchmark direction 가드레일 추가
  - admin panel에 runtime input request/provide 레지스트리 추가
  - `_docs/OPERATOR_INPUTS.json` prompt-safe artifact 추가
  - shell/template env bridge 추가
- Remaining:
  - job detail read-only visibility
  - app별 UI benchmark note artifact
  - AI-generated request draft + operator approval

## 6.1 Current Slice Update
- `DONE`
  - job detail/operator view에 follow-up contract와 lineage 노출
  - assistant log-analysis 한정 `tool-using diagnosis loop` 첫 슬라이스
- 이번 슬라이스 범위
  - feature flag `assistant_diagnosis_loop`
  - `_docs/ASSISTANT_DIAGNOSIS_TRACE.json`
  - `log_lookup -> repo_search -> memory_search` 순차 evidence pack
  - assistant log-analysis / assistant chat prompt에 tool diagnosis context 추가
  - job detail debug tab과 assistant chat 응답에 diagnosis trace 노출
  - admin panel에서 diagnosis trace scope/tool/recent trace 비교 뷰 제공
  - admin panel에서 selected/compare trace diff와 tool query/status 차이 노출
- 다음 남은 것
  - diagnosis trace 장기 효과성/빈도 상관관계 보강

## 7. Exit Criteria For This Roadmap Slice
- operator가 `_docs/VECTOR_SHADOW_INDEX.json`로 shadow candidate를 볼 수 있다.
- operator가 `MEMORY_SEARCH_RESULT.json`에서 source=`db|vector`와 fallback 여부를 볼 수 있다.
- operator가 `_docs/LANGGRAPH_PLANNER_SHADOW.json`에서 planner draft/evaluate/refine shadow trace를 볼 수 있다.
- operator가 `_docs/LANGGRAPH_RECOVERY_SHADOW.json`에서 recovery analyze/decide/fix/retest shadow trace를 볼 수 있다.
- operator가 `_docs/FOLLOWUP_BACKLOG_TASK.json`으로 어떤 backlog candidate가 다음 planner 입력으로 연결됐는지 볼 수 있다.
- feature flag off면 기존 동작이 바뀌지 않는다.
- SQLite source-of-truth는 그대로 유지된다.
- full regression이 통과한다.
