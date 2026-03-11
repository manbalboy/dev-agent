# Phase 3 Memory Runtime Design

## 1. Purpose
- Phase 3의 목적은 이 시스템을 단순한 `자동 실행기`가 아니라 `스스로 학습하며 24시간 동작하는 개발 동료`로 끌어올리는 것이다.
- 핵심은 memory artifact를 파일로 남기는 수준을 넘어서, `메모리 DB에 적재 -> 검색 -> 평가 -> 다음 행동 반영`까지 연결하는 것이다.
- Phase 3에서는 memory가 `source of truth`를 대체하지 않는다.
  - 최종 결정은 계속 workflow, hard gate, review, improvement loop가 맡는다.
  - memory는 더 나은 계획/수정/리뷰/우선순위 결정을 돕는 `auditable retrieval layer`로 사용한다.

## 2. Decision
- Decision: `Phase 3 = Memory Runtime`
- Why:
  - 현재 코드는 이미 workflow runtime, node_runs, resume/recovery, adaptive memory artifact, retrieval artifact, feature-flag rollout까지 들어가 있다.
  - 아직 없는 것은 `memory를 장기적으로 축적하고 실제 다음 행동을 바꾸는 durable runtime`이다.
  - 따라서 기존 실행 계획의 `Continuous Job Operations`보다 먼저 `Memory Runtime`을 Phase 3로 두는 것이 프로그램 목적에 더 맞다.
- Follow-up:
  - 기존 `Continuous Job Operations`는 다음 phase로 이동한다.
  - 즉, 운영 hardening은 memory runtime 이후에 진행한다.

## 3. Phase 3 Goal
- 같은 repo/app에서 반복되는 실패를 줄인다.
- repo convention 위반을 줄인다.
- planner/reviewer/coder/fixer가 과거 성공/실패를 참고해 더 나은 결정을 하게 만든다.
- review/improvement 결과가 memory DB에 누적되고, 다음 작업 backlog와 strategy 제안에 반영되게 만든다.
- 운영자는 어떤 memory가 왜 선택되었고, 실제로 도움이 되었는지 추적할 수 있어야 한다.

## 4. Current Status Snapshot

| Area | Status | Already Exists | Not Yet Done |
| --- | --- | --- | --- |
| Workflow runtime foundation | `DONE` | workflow selection, registry, node_runs, resume/recovery, workflow editor, job detail workflow visibility | memory runtime 전용 trace는 부족 |
| Structured memory artifacts | `DONE` | `MEMORY_LOG.jsonl`, `DECISION_HISTORY.json`, `FAILURE_PATTERNS.json`, `CONVENTIONS.json` 생성 | file artifact가 여전히 1차 생성물이며 DB query source 전환은 아직 아님 |
| Controlled retrieval | `PARTIAL` | `MEMORY_SELECTION.json`, `MEMORY_CONTEXT.json`, `MEMORY_TRACE.json`, planner/reviewer/coder prompt injection, DB-backed retrieval with file fallback | rejected candidate trace, pure-DB rollout control 부족 |
| Convention extraction | `PARTIAL` | repo 구조/manifest/test pattern 기반 convention 추출 | convention confidence lifecycle, 수동 승인/차단, 지속 보정 부족 |
| Memory quality scoring | `PARTIAL` | `MEMORY_FEEDBACK.json`, `MEMORY_RANKINGS.json`, banned-memory avoidance, DB stale decay/effectiveness refresh, manual promote/ban/archive override | 장기 통계, cross-repo ranking policy, richer ranking explanations 부족 |
| Strategy shadow | `PARTIAL` | `STRATEGY_SHADOW_REPORT.json` 생성, memory-aware strategy 비교 | shadow 결과를 backlog와 rollout 의사결정으로 묶는 계층 부족 |
| Memory storage backend | `PARTIAL` | SQLite-based `memory_runtime.db`, canonical tables, DB-first retrieval, autonomous backlog candidate table | richer retrieval query layer, backlog approval workflow, cross-workspace health metrics 부족 |
| Artifact -> DB ingest | `PARTIAL` | job 종료 시 artifact ingest skeleton이 `memory_runtime.db`로 episodic/decision/failure/convention/retrieval/feedback/backlog candidate를 적재 | backfill, migration, ingest health metrics 부족 |
| Autonomous dev companion loop | `PARTIAL` | next tasks / improvement artifacts를 DB backlog candidate queue로 적재 | recurring failure clustering, self-initiated improvement queue, operator approve->execution bridge 없음 |
| Operator memory tooling | `PARTIAL` | feature flag UI, admin metrics, job detail retrieval source/trace visibility, memory search/detail, promote/ban UI, backlog queue visibility | retrieval trace filter/search, memory source artifact drilldown, backlog approval controls 부족 |

## 5. What Is Already Implemented

### 5.1 Runtime Foundation
- workflow 실행/선택/추적 기반은 이미 있다.
- 포함 항목:
  - `job > app > default` workflow resolution
  - executor registry
  - node run persistence
  - interrupted cleanup / resume
  - workflow editor / diff / preview / node binding
- 관련 문서:
  - [JOB_WORKFLOW_RESOLUTION.md](./JOB_WORKFLOW_RESOLUTION.md)
  - [NODE_RUNS_SCHEMA.md](./NODE_RUNS_SCHEMA.md)

### 5.2 Memory Artifact Generation
- job 실행 과정에서 아래 artifact가 생성된다.
  - `MEMORY_LOG.jsonl`
  - `DECISION_HISTORY.json`
  - `FAILURE_PATTERNS.json`
  - `CONVENTIONS.json`
  - `MEMORY_SELECTION.json`
  - `MEMORY_CONTEXT.json`
  - `MEMORY_FEEDBACK.json`
  - `MEMORY_RANKINGS.json`
  - `STRATEGY_SHADOW_REPORT.json`
- 현재 의미:
  - memory schema의 초안은 존재한다.
  - retrieval과 scoring도 파일 기반으로는 연결돼 있다.
  - 현재는 이 artifact들이 `memory_runtime.db`에도 적재된다.
- 한계:
  - retrieval은 DB 우선으로 전환됐지만 artifact fallback을 유지하는 혼합 단계다.
  - 검색/평가/차단/승격 이력이 운영 UI와 trace 기준으로는 아직 노출되지 않는다.

### 5.3 Feature Flag and Safe Rollout
- adaptive 기능은 feature flag로 on/off 가능하다.
- 현재 플래그:
  - `memory_logging`
  - `memory_retrieval`
  - `convention_extraction`
  - `memory_scoring`
  - `strategy_shadow`
- 현재 의미:
  - shadow -> opt-in -> partial rollout 구조를 유지할 수 있다.
- 한계:
  - 기능이 `DB runtime` 기준으로 분리되어 있지 않다.
  - retrieval source와 fallback 여부를 운영자가 UI에서 명확히 확인하기 어렵다.

### 5.4 Prototype Memory Store Exists
- `app/memory/fix_store.py`에 zero-dependency fix history store가 있다.
- 현재 의미:
  - problem/diff_summary/score_delta 기반의 cross-job retrieval 실험체는 있다.
- 한계:
  - Phase 3의 canonical backend로 쓰기에는 범위가 좁다.
  - episodic / decision / convention / failure pattern을 통합 관리하지 못한다.

### 5.5 Memory DB Foundation Exists
- `app/memory/runtime_store.py`에 SQLite 기반 canonical memory store가 추가되었다.
- `app/memory/runtime_ingest.py`가 현재 `_docs/MEMORY_*.json` 산출물을 DB로 ingest한다.
- 현재 의미:
  - `memory_entries`, `memory_evidence`, `memory_feedback`, `memory_retrieval_runs`, `memory_backlog_candidates` 스키마가 존재한다.
  - improvement 단계 종료 후 artifact가 `memory_runtime.db`에 자동 반영된다.
- 한계:
  - retrieval source는 DB 우선으로 전환됐지만 아직 fallback을 유지하는 혼합 단계다.
  - backlog candidate는 적재되지만 승인/실행 큐까지 이어지지는 않는다.

### 5.6 DB-Backed Retrieval Exists
- `app/orchestrator.py`의 retrieval 생성은 이제 `memory_runtime.db`를 우선 조회하고, 후보가 없을 때만 file artifact로 fallback한다.
- 현재 의미:
  - planner/reviewer/coder prompt에 들어가는 `MEMORY_SELECTION.json`, `MEMORY_CONTEXT.json`가 DB 기반 memory를 반영한다.
  - `MEMORY_TRACE.json`에 source / fallback / route별 selected memory가 기록된다.
  - 기존 artifact 포맷을 유지해서 prompt builder와 dashboard 영향 범위를 최소화했다.
- 한계:
  - rejected candidate나 ranking 이유까지는 trace에 남기지 않는다.
  - pure-DB source 강제나 backfill health metric은 아직 없다.

### 5.7 DB Ranking Refresh Exists
- `app/memory/runtime_store.py`가 feedback/retrieval history를 바탕으로 ranking을 다시 계산한다.
- 현재 의미:
  - `baseline_score / baseline_confidence` 위에 retrieval effectiveness와 stale penalty를 반영한다.
  - retrieval 전에 ranking refresh가 실행되어 decayed/banned memory가 우선순위에 반영된다.
  - ingest 시점에도 DB writeback이 일어나 memory state가 file artifact에만 머물지 않는다.
- 한계:
  - ranking reason은 저장되지만 장기 통계 리포트는 아직 없다.
  - cross-repo policy나 state transition audit trail은 아직 약하다.

### 5.8 Autonomous Backlog Queue Exists
- `app/memory/runtime_ingest.py`가 이제 `IMPROVEMENT_BACKLOG.json`, `NEXT_IMPROVEMENT_TASKS.json`, `QUALITY_TREND.json`, `STRATEGY_SHADOW_REPORT.json`에서 backlog 후보를 추출해 `memory_backlog_candidates`에 적재한다.
- 현재 의미:
  - improvement 결과가 file artifact에만 남지 않고 DB 후보 큐로 연결된다.
  - operator는 admin 화면에서 현재 repo/app/workflow 기준 backlog 후보를 읽기 전용으로 확인할 수 있다.
- 한계:
  - backlog candidate는 아직 `candidate` 상태로만 적재된다.
  - approve / dismiss / execute 같은 운영 action은 아직 없다.

## 6. What Phase 3 Must Deliver

### 6.1 Memory DB
- 목표:
  - Phase 2의 file artifact를 `DB-backed canonical memory`로 전환한다.
- 필수:
  - memory entry canonical schema
  - stable `memory_id`
  - `repo/app/workflow/job/attempt` scope
  - state: `candidate/promoted/decayed/banned/archived`
  - created_at / updated_at / last_used_at / last_feedback_at
- 원칙:
  - 1차 구현은 `SQLite-first`로 간다.
  - 이후 필요 시 Postgres/pgvector로 lift할 수 있게 schema를 설계한다.
  - vector search는 필수가 아니라 선택적 확장이다.

### 6.2 Artifact -> DB Ingestion
- 목표:
  - 현재 `_docs/MEMORY_*.json` 산출물을 DB에 적재한다.
- 필수:
  - ingest job 또는 ingest function
  - 중복 방지 규칙
  - source artifact lineage 기록
  - migration/backfill 경로

### 6.3 DB-Backed Retrieval Runtime
- 목표:
  - planner/reviewer/coder/fixer가 file-based artifact가 아니라 memory DB에서 retrieval 받도록 바꾼다.
- 필수:
  - same repo/app 우선
  - recent/high-confidence 우선
  - top-k 제한
  - token budget 제한
  - banned memory 제외
  - retrieval trace 기록
- 산출물:
  - `MEMORY_SELECTION.json`
  - `MEMORY_CONTEXT.json`
  - `MEMORY_TRACE.json` 또는 동등 trace payload

### 6.4 Feedback and Ranking Runtime
- 목표:
  - memory를 실제 결과로 평가하고 promote/decay/ban 상태를 갱신한다.
- 필수:
  - retrieval 후 사용 여부 기록
  - score delta / regression / repeated failure 반영
  - success-rate / freshness / confidence 기반 ranking
  - stale memory decay

### 6.5 Convention Runtime
- 목표:
  - convention을 static artifact가 아니라 반복적으로 보정되는 memory type으로 바꾼다.
- 필수:
  - convention evidence path 저장
  - confidence / conflict handling
  - repo-level approved conventions view

### 6.6 Autonomous Backlog Generation
- 목표:
  - 이 시스템이 단순히 주어진 issue만 처리하는 것이 아니라, memory를 기반으로 `다음에 고칠 가치가 큰 것`을 제안하게 만든다.
- 필수:
  - recurring failure clustering
  - persistent low category aggregation
  - recommended next action synthesis
  - operator 승인 가능한 backlog queue
- 주의:
  - Phase 3에서는 자동 merge/autonomous execution까지 가지 않는다.
  - 우선은 `suggest -> queue -> operator confirm` 구조를 목표로 한다.

### 6.7 Operator Memory UI
- 목표:
  - 운영자가 memory runtime을 믿고 쓸 수 있게 만든다.
- 필수:
  - memory search
  - retrieval trace by job
  - selected memory / rejected memory 표시
  - manual promote / ban / archive
  - memory source artifact 링크

## 7. Phase 3 Work Packages

### Phase 3-A. Memory DB Foundation
- Status:
  - `PARTIAL`
- Deliverables:
  - `memory_entries`
  - `memory_evidence`
  - `memory_feedback`
  - `memory_retrieval_runs`
  - `memory_backlog_candidates`
- Success:
  - memory artifact를 DB로 적재할 수 있다.

### Phase 3-B. Ingestion and Canonicalization
- Status:
  - `PARTIAL`
- Deliverables:
  - artifact -> DB ingest pipeline
  - idempotent upsert
  - source lineage
- Success:
  - job 종료 후 memory artifact가 자동으로 DB에 반영된다.

### Phase 3-C. Retrieval Runtime
- Deliverables:
  - planner/reviewer/coder/fixer용 retrieval service
  - retrieval trace
  - prompt injection source 전환
- Success:
  - retrieval이 file scan이 아니라 DB query로 동작한다.

### Phase 3-D. Feedback and Ranking
- Deliverables:
  - promote/decay/ban engine
  - retrieval effectiveness metrics
  - stale memory cleanup
- Success:
  - 도움이 된 memory와 오염된 memory가 분리 관리된다.

### Phase 3-E. Autonomous Backlog
- Status:
  - `PARTIAL`
- Deliverables:
  - recurring failure summarizer
  - memory-backed backlog candidates
  - next-action recommendation contract
- Success:
  - 시스템이 memory 기반으로 `다음 개선 작업`을 제안한다.

### Phase 3-F. Operator Visibility
- Status:
  - `PARTIAL`
- Deliverables:
  - memory search UI
  - retrieval trace UI
  - promote/ban controls
- Success:
  - 운영자가 memory runtime의 결정 근거를 화면에서 확인 가능하다.

## 8. Explicitly Out of Scope
- memory가 workflow 제어권을 직접 갖는 구조
- LLM이 자유롭게 다음 node를 선택하는 구조
- general-purpose knowledge graph
- zero-downtime / HA
- multi-worker distributed memory coordination
- fully autonomous self-triggered coding without operator gate

## 9. Completion Criteria
- 각 job 종료 후 structured memory가 DB에 적재된다.
- planner/reviewer/coder/fixer가 DB-backed retrieval 결과를 사용한다.
- retrieval trace에서 `왜 이 memory가 선택됐는지`를 볼 수 있다.
- feedback 결과로 memory가 promote/decay/ban 된다.
- 반복 실패/낮은 품질 카테고리가 backlog candidate로 축적된다.
- 운영자가 memory search, memory inspect, memory control을 UI에서 수행할 수 있다.

## 10. One-Line Summary
- Phase 2가 `adaptive memory를 붙이는 단계`였다면,
- Phase 3는 `memory를 durable DB runtime으로 바꾸고 실제 다음 행동을 바꾸게 만드는 단계`다.
