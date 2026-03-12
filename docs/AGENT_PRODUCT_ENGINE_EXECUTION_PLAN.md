# Agent Product Engine Execution Plan

## Core Operating Principles
- 상위 운영 원칙 문서: [AI_AGENT_OPERATING_PRINCIPLES.md](./AI_AGENT_OPERATING_PRINCIPLES.md)
- 핵심 강제 규칙:
  - 완성형을 한 번에 만들지 않고 항상 MVP부터 시작한다.
  - 코드 생성보다 제품 정의와 품질 평가를 우선한다.
  - 같은 개선을 반복하지 않고, 품질이 오르지 않으면 전략을 바꾼다.
  - 모든 중요한 설계/범위/리뷰 결정은 문서로 남긴다.

## Goal
- Job 입력을 받으면 대상 레포를 분석하고 프로젝트를 생성/확장한다.
- 구현 후 끝나지 않고 리뷰와 테스트를 바탕으로 스스로 품질을 높인다.
- 365일 운영은 중요하지만 후순위로 두고, 먼저 "계속 고도화되는 제품 개발 엔진"을 완성한다.

## Delivery Order
1. Product pipeline 강제력 확보
2. Improvement loop 실행력 확보
3. Memory runtime / adaptive learning engine 확보
4. Agentic runtime adoption (`MCP` / `Qdrant` / `LangGraph`) 확보
5. Job recovery / long-running 운영성 확보
6. Observability / operator tooling 확보
7. Durable backend / workspace hygiene 확보
8. 무중단/HA 도입

## AI Role Policy
- `Gemini`: 제품 정의, 기획, 리뷰, 우선순위 판단의 주 역할
- `Codex`: 구현, 수정, 퍼블리싱, 리팩토링 같은 코더/전문가 역할의 주 역할
- 문서화, 에스컬레이션, 보조 분석 같은 보조 역할도 현재 기본 경로는 `Codex`로 처리한다.
- `Claude` / `Copilot` 이름은 일부 레거시 route/template alias로만 남아 있을 수 있다.
- 실행 코드는 논리 역할(`planner/reviewer/coder/...`)을 직접 하드코딩하지 않고 `config/ai_role_routing.json`으로 라우팅한다.
- 역할군을 바꿀 때는 파이썬 코드 대신 라우팅 파일과 provider-specific 템플릿(`planner__gemini`, `coder__codex` 등)을 수정한다.

## Phase Overview

### Phase 0. Baseline Lock
- 목적: 현재 stage/문서/리뷰/개선 구조를 표준 계약으로 고정한다.
- 완료 조건:
  - stage contract / pipeline analysis / review schema가 코드와 문서에서 일치
  - 테스트가 산출물 존재뿐 아니라 계약 준수까지 확인

### Phase 1. Product Pipeline Core
- 목적: 자동 코딩이 아니라 제품 정의 -> 스캐폴딩 -> MVP 구현을 강제한다.
- 핵심 작업:
  - `idea_to_product_brief`
  - `generate_user_flows`
  - `define_mvp_scope`
  - `architecture_planning`
  - `project_scaffolding`
- 완료 조건:
  - 구현 단계가 제품 문서 없이 실행되지 않음
  - 빈 레포와 기존 레포를 구분해 다른 scaffold 전략을 가짐

### Phase 2. Self-Improvement Loop
- 목적: review 결과가 실제 다음 실행을 제어하게 만든다.
- 핵심 작업:
  - `product_review` 증거 기반 강화
  - `improvement_stage` 결과를 후속 planner/coder 입력으로 연결
  - 반복/정체/하락 감지 시 전략 변경 실행
- 완료 조건:
  - improvement 결과가 파일 생성에서 끝나지 않고 다음 행동을 바꿈

### Phase 3. Memory Runtime
- 목적: adaptive memory를 durable DB runtime으로 올려 `스스로 성장하는 24시간 개발 동료`의 학습 계층을 만든다.
- 핵심 작업:
  - memory DB schema / ingest
  - DB-backed retrieval
  - feedback / ranking
  - convention runtime
  - autonomous backlog generation
- 완료 조건:
  - memory가 planner/reviewer/coder의 다음 행동을 auditable하게 바꾼다.
  - 상세 설계/현재 상태: [PHASE3_MEMORY_RUNTIME_DESIGN.md](./PHASE3_MEMORY_RUNTIME_DESIGN.md)

### Phase 4. Agentic Runtime Adoption
- 목적: 이미 있는 workflow/runtime 위에 외부 agent framework를 작은 단위부터 붙여 tool 사용, semantic retrieval, subgraph 실행력을 확장한다.
- 핵심 작업:
  - MCP tool layer
  - Qdrant vector retrieval
  - LangGraph planner/recovery subgraph
- 완료 조건:
  - top-level orchestrator를 유지한 채 외부 프레임워크가 shadow/opt-in/default 순서로 안전하게 들어간다.
  - 현재 상태: planner/recovery는 LangGraph shadow trace까지 진입
  - Phase 5 전에 `approved backlog -> next job enqueue` 첫 슬라이스와 `FOLLOWUP_BACKLOG_TASK.json` bridge까지 연결됨
  - follow-up job contract 메타(`job_kind`, `parent_job_id`, `backlog_candidate_id`)까지 분리됨
  - `FAILURE_PATTERNS.json` 기반 recurring failure backlog 입력도 연결됨
  - 다음 남은 연결고리: operator backlog UX 보강, diagnosis trace 장기 효과성/빈도 상관관계
  - `tool-using diagnosis loop` 첫 슬라이스는 assistant log-analysis 경로에 연결됨
  - assistant chat과 job detail에도 diagnosis trace가 노출됨
  - admin panel에도 diagnosis trace 비교 뷰가 노출됨
  - admin panel에서 selected/compare trace diff까지 가능
  - UI 품질 가드레일과 운영자 runtime input registry 첫 슬라이스가 들어감
  - 모바일 앱 분류 작업 기준은 [MOBILE_APP_DEVELOPMENT_MODE_RULESET.md](./MOBILE_APP_DEVELOPMENT_MODE_RULESET.md) 를 따른다
  - `workspace_app.sh` 에 `expo-android`, `expo-ios`, `rn-android`, `rn-ios` 실행 프리셋이 추가됨
  - admin 운영 지표에 앱 실행 상태 카드가 추가되어 최근 mobile/web 실행 모드와 명령을 읽을 수 있음
  - 앱 분류 저장소는 테스트 단계 이후 `_docs/MOBILE_APP_CHECKLIST.md` baseline artifact를 자동 생성함
  - 운영자 입력 관리에는 AI/템플릿 기반 request draft 추천과 승인 등록 경로가 추가됨
  - 상세 설계/도입 순서: [PHASE4_AGENTIC_RUNTIME_ADOPTION_PLAN.md](./PHASE4_AGENTIC_RUNTIME_ADOPTION_PLAN.md)
  - 단기 우선순위/작은 슬라이스 기준: [FUTURE_DIRECTION_PRIORITY_ROADMAP.md](./FUTURE_DIRECTION_PRIORITY_ROADMAP.md)
  - UI 품질/운영자 입력 상세: [UI_QUALITY_AND_OPERATOR_INPUTS_PLAN.md](./UI_QUALITY_AND_OPERATOR_INPUTS_PLAN.md)

### Phase 5. Continuous Job Operations
- 목적: 장기 운영 시 stuck job / provider outage / worker restart에 견디게 만든다.
- 핵심 작업:
  - heartbeat / timeout / stuck detection
  - orphan `running` recovery
  - failure classification
  - retry policy split
- 완료 조건:
  - worker 재시작 후 running 고착 작업을 자동 정리/복구
  - 상세 설계/확장 방향: [PHASE5_CONTINUOUS_JOB_OPERATIONS_EXPANSION_PLAN.md](./PHASE5_CONTINUOUS_JOB_OPERATIONS_EXPANSION_PLAN.md)
  - 첫 구현 권장 슬라이스: `5-A1 Runtime Recovery Trace`

### Phase 6. Observability
- 목적: 실패 원인과 품질 추세를 운영자가 즉시 파악할 수 있게 만든다.
- 핵심 작업:
  - stage latency
  - failure rate by stage/provider
  - review score trend
  - alert / dead-letter / recovery action

### Phase 7. Durable Runtime
- 목적: 365일 운영을 위한 저장소/워크스페이스/백업 기반을 강화한다.
- 핵심 작업:
  - durable queue/state
  - workspace cleanup policy
  - backup / restore
  - periodic self-check

### Phase 8. Zero-Downtime / HA
- 목적: 마지막 단계에서 다중 인스턴스와 무중단 배포를 도입한다.
- 핵심 작업:
  - multi-worker claim model
  - external queue / lock
  - rolling restart / health-based traffic shift

## P0 Execution Backlog

### P0-1. Explicit `project_scaffolding` Stage
- Why:
  - 현재는 scaffold가 구현 단계 내부에 암묵적으로 섞여 있다.
  - 신규 프로젝트 생성과 기존 프로젝트 확장을 같은 단계에서 처리하면 품질이 흔들린다.
- Deliverables:
  - `_docs/SCAFFOLD_PLAN.md`
  - `_docs/BOOTSTRAP_REPORT.json`
  - workflow node / stage enum / contract update
- Success Criteria:
  - architecture 이후 scaffold 단계가 반드시 실행
  - 레포 상태(existing/empty/partial)와 추천 bootstrap action이 기록

### P0-2. Design Docs to PLAN/CODER/REVIEWER Binding
- Why:
  - 제품 문서가 생성돼도 현재 PLAN/CODER가 이를 직접 강제하지 않는다.
- Deliverables:
  - planner/coder/reviewer prompt input 확장
  - quality gate에 설계 일치도 추가
- Success Criteria:
  - 제품 정의 문서가 구현 계약으로 사용됨

### P0-3. Review Strengthening
- Why:
  - 현재 review는 휴리스틱 비중이 높아 제품 품질 평가기로 보기 어렵다.
- Deliverables:
  - 증거 기반 점수 reason
  - category별 remediation guidance
  - stronger validation

### P0-4. Improvement Execution Link
- Why:
  - improvement backlog는 생성되지만 다음 실행을 직접 제어하지는 않는다.
- Deliverables:
  - next task consumer
  - scope restriction enforcement
  - strategy change propagation

### P0-5. Running Job Recovery
- Why:
  - worker restart 시 `running` 상태 고착이 가장 큰 장기 운영 리스크다.
- Deliverables:
  - heartbeat/state cleanup
  - orphan running recovery policy

## Current Sprint
- Sprint goal: `현재 상태를 냉정하게 기준선으로 고정하고, 구조 리스크 축소와 Phase 5 운영 신뢰성 진입 준비를 시작한다.`
- In scope:
  - current state gap report 유지
  - `dashboard.py` 점진 분리
  - assistant/provider prompt 및 실행 경로 분리
  - agent config / template safety 로직을 별도 runtime 모듈로 분리
  - `orchestrator.py` 점진 분리의 다음 슬라이스 정의 및 착수
  - commit summary / PR summary / code change summary helper 실행을 `summary_runtime`으로 추출
  - design/publish/copywriter/documentation 보조 단계를 `content_stage_runtime`으로 추출
  - review/fix stage 실행을 `review_fix_runtime`으로 추출
  - planner stage(one-shot / graph / shadow trace)를 `planner_runtime`으로 추출
  - implement/coder stage를 `implement_runtime`으로 추출
  - workflow dispatch / node handler 본문을 `workflow_node_runtime`으로 추출
  - workflow pipeline dispatch / node-run bookkeeping을 `workflow_pipeline_runtime`으로 추출
  - git/github provider execution(`push/create_pr/url lookup`)을 `provider_runtime`으로 추출
  - docker preview / port allocation / PR preview helper를 `preview_runtime`으로 추출
  - app type 판별 / non-web UX skip helper를 `app_type_runtime`으로 추출
  - product-definition stage/fallback/contract helper를 `product_definition_runtime`으로 추출
  - improvement stage/strategy helper를 `improvement_runtime`으로 추출
  - UX screenshot / UX_REVIEW markdown helper를 `ux_review_runtime`으로 추출
  - workflow resume / workflow loading helper를 `workflow_resolution_runtime`으로 추출
  - stage markdown snapshot / docs commit helper를 `docs_snapshot_runtime`으로 추출
  - job detail/runtime signals/log summary/operator input helper를 `dashboard_job_runtime`으로 추출
  - runtime input serialization / draft / request / provide helper를 `dashboard_runtime_input_runtime`으로 추출
  - admin metrics / assistant diagnosis aggregation helper를 `dashboard_admin_metrics_runtime`으로 추출
  - `runtime_recovery_trace` helper와 `_docs/RUNTIME_RECOVERY_TRACE.json` artifact를 추가
  - `failure_classification` helper와 normalized failure class summary를 추가
  - failure classification에 `provider_hint / stage_family` mapping을 추가
  - dashboard / job detail에 failure class visibility를 추가
  - 모바일 앱 개발 모드 규칙 문서 추가 및 planner/coder/reviewer prompt 반영
  - production-readiness 기본선 유지
  - 회귀 테스트 유지
- Out of scope:
  - 라이선스 정책 결정
  - 운영 시크릿 실제 로테이션
  - 파괴적 Git 히스토리 정리

## Acceptance Criteria
- 핵심 문서가 현재 소스 상태와 충돌하지 않는다.
- production-readiness 기본선이 CI와 저장소 위생 검사로 자동 확인된다.
- 위험 플래그 기본값은 opt-in 상태를 유지한다.
- 전체 회귀 테스트가 통과한다.

## Current Reality Check
- 현재 상태는 `강한 기반을 가진 고급 프로토타입`이다.
- 하지만 아직 `스스로 성장하는 24시간 개발 동료`라고 부르기에는 이르다.
- 가장 큰 실제 부족분은 아래다.
  - [app/orchestrator.py](../app/orchestrator.py) 중심의 구조 리스크
  - Phase 5 operator ops surface / dead-letter list 미완
  - Phase 4 기능의 shadow/opt-in 비중이 아직 높음
  - self-growing bridge의 장기 효과 검증 부족
- 현재 기준 대형 파일 상태:
  - [app/dashboard.py](../app/dashboard.py): `3637` lines
  - [app/orchestrator.py](../app/orchestrator.py): `6068` lines
- 즉, `dashboard.py`는 1차 전환 기준 아래로 내려왔고, `orchestrator.py`도 failure transition/runtime 분리와 worker restart safety baseline까지 올라왔다. 다만 핵심 구조 리스크는 여전히 orchestrator 쪽에 남아 있다.
- 상세 판정은 [CURRENT_STATE_GAP_REPORT.md](./CURRENT_STATE_GAP_REPORT.md) 를 따른다.

## Next Priority Shift
- 현재부터의 우선순위는 `새 기능 폭 확대`보다 아래 순서를 따른다.
  1. Phase 5-G `Minimal Operator Ops Surface`
  2. `orchestrator.py`의 남은 runtime 슬라이스 분리
  3. self-growing bridge 효과성 검증
- 현재 가장 자연스러운 다음 후보:
  - `remaining runtime split`
  - 이후 `orchestrator` 남은 슬라이스 분리
  - 현재 판단상 provider burst 계측, cooldown 전이, provider quarantine, provider circuit-breaker, planner/reviewer alternate route fallback, worker startup sweep trace, restart-safe requeue reason, running node/job mismatch audit, dead-letter 재큐잉 액션, operator note trail, dead-letter list / recovery history summary, provider outage history, startup sweep history, dead-letter / recovery action drilldown, recovery action groups, operator action trail까지는 올라왔다. `workspace_repository_runtime`, `preview_runtime`, `app_type_runtime`, `product_definition_runtime`, `improvement_runtime`, `ux_review_runtime`까지 분리됐고, 다음 조각은 memory helper 축소와 self-growing bridge 효과성 검증 쪽이다.
