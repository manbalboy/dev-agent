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
6. Operator control plane / third-party integration registry 확보
7. Durable backend / patch control / workspace hygiene 확보
8. Nonlinear engine / self-growing strong closure
9. 무중단/HA 도입

## Goal-Closure Priority Reset
- phase 순서와 별도로, `진짜 목표에 도달하려면 무엇을 먼저 닫아야 하는가` 기준은 [GOAL_CLOSURE_PRIORITY_RESET.md](./GOAL_CLOSURE_PRIORITY_RESET.md) 를 따른다.
- 현재 기준 핵심 필수는 아래다.
  - `self-learning loop`를 `partial -> strong`
  - `AI fallback`을 `partial -> strong`
  - `nonlinear runtime / vector retrieval / graph-subgraph`를 `partial -> strong`
  - `integration/operator control`을 `partial -> strong`
  - `앱 개발 모드 + emulator E2E`를 `baseline -> strong`
  - `patch/update/durable runtime` baseline 닫기
- `operator-facing graph/subgraph visualization baseline`은 Phase 8에서 같이 올린다.
- `한/영 UI 완전 다국어`, `full visual editor` 급 graph workflow 편집기는 여전히 후순위다.

## AI Role Policy
- 상세 기준 문서: [AI_ROLE_EXECUTION_POLICY.md](./AI_ROLE_EXECUTION_POLICY.md)
- `Gemini`: 제품 정의, 기획, 리뷰, 테스트 결과 해석, 품질 게이트 판단, commit/PR/escalation 요약의 주 역할
- `Codex`: 구현, 수정, 퍼블리싱, 리팩토링, 기술 문서 실제 작성의 주 역할
- `bash`: pytest / npm test / e2e / emulator 같은 실제 실행 역할
- `documentation` route는 `Codex`, `commit_summary / pr_summary / escalation`은 `Gemini`, `tester`는 `bash`, `test_reviewer`는 `Gemini` baseline route로 둔다.
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
  - `mobile_e2e_runner.sh` 로 Android/iOS emulator baseline E2E 실행 계약이 추가됨
  - `_docs/MOBILE_E2E_RESULT.json` 이 마지막 platform/target/runner/command/status를 기록함
  - job detail workflow 탭과 admin 운영 지표가 마지막 모바일 E2E 결과를 직접 surface함
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

### Phase 6. Operator Control Plane And Integration Registry
- 목적: 남아 있는 Phase 5 operator control 잔여 항목과 서드파티 통합 관리 계층을 하나의 control plane으로 올린다.
- 핵심 작업:
  - third-party integration registry
  - runtime input / env bridge upgrade
  - AI recommendation -> operator approval -> implementation 연결
  - failed job / provider fallback / restart-safe action drilldown 통합
- 완료 조건:
  - 운영자가 `Google Maps` 같은 통합 항목을 등록하고 승인/보류할 수 있다.
  - AI는 승인된 통합의 가이드와 env 이름을 기준으로 구현한다.
  - 상세 설계/도입 순서: [PHASE6_OPERATOR_CONTROL_AND_INTEGRATION_REGISTRY_PLAN.md](./PHASE6_OPERATOR_CONTROL_AND_INTEGRATION_REGISTRY_PLAN.md)
  - 현재 상태: `6-C1 planner recommendation draft`, `6-C2 operator approve/reject action`, `6-C3 approval trail`, `6-B2 missing integration input reason surface`, `6-B3 env bridge policy hardening`, `6-D1 prompt-safe guide summary`, `6-D2 code pattern/snippet hint`, `6-D3 verification checklist injection`, `6-E1 failed job operator approval boundary`, `6-F1 integration usage trail`, `6-F2 missing-input / auth / quota facet`, `6-F3 integration health summary`까지 implemented
  - 다음 우선순위: `remaining runtime split` 잔여 정리

### Phase 7. Durable Runtime
- 목적: 365일 운영을 위한 저장소/워크스페이스/백업 기반을 강화한다.
- 핵심 작업:
  - patch/update detection
  - patch run state / progress
  - separate updater service
  - durable queue/state
  - workspace cleanup policy
  - backup / restore
  - periodic self-check
  - 상세 설계/도입 순서: [PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md](./PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md)
  - 현재 상태: `7-A1 patch status detection baseline`, `7-A2 patch run state / progress`, `7-B1 separate updater service`, `7-B2 service drain / stop / restart`, `7-C1 post-update health check`, `7-C2 rollback baseline`, `7-D1 backup baseline + patch coupling`, `7-D2 restore action / backup verification`, `7-E1 durable runtime / workspace hygiene baseline`, `7-E2 security / TLS governance baseline`, `7-E3 periodic self-check baseline`, `7-E4 secret rotation / reverse-proxy TLS runbook baseline`, `7-E5 self-check alert lifecycle baseline`, `7-E6 self-check alert routing baseline` implemented
  - 남은 항목: `remaining runtime split / read-service long-tail`, `durable backend / self-check alert provider policy hardening`, `LICENSE / 정책 의사결정`
  - 원칙: 엔진 승격 blocker 성격의 남은 항목은 [PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md](./PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md) 의 `8-E. Phase 7 Carry-Over Enabling Track`으로 넘겨 관리한다.

### Phase 8. Nonlinear Engine / Self-Growing Strong Closure
- 목적: 이미 partial/shadow/opt-in 으로 들어온 비선형 runtime, vector retrieval, graph/subgraph, self-growing loop를 strong/operator-visible/primary-candidate 상태로 승격한다.
- 핵심 작업:
  - nonlinear runtime / reusable subgraph promotion
  - planner / reviewer / coder 후보 vector retrieval rollout
  - self-growing long-horizon strategy loop closure
  - operator-facing graph/subgraph visualization baseline
  - Phase 7 carry-over enabling track 정리
- 완료 조건:
  - 비선형 runtime이 shadow-only가 아니다.
  - vector retrieval이 `memory_search` 밖의 핵심 path로 올라온다.
  - self-growing loop가 장기 효과를 근거로 실제 다음 전략을 바꾼다.
  - operator가 workflow와 graph/subgraph decision path를 구조적으로 읽을 수 있다.
  - 상세 설계/도입 순서: [PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md](./PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md)

### Phase 9. Zero-Downtime / HA
- 목적: 엔진 closure 이후 마지막 단계에서 다중 인스턴스와 무중단 배포를 도입한다.
- 핵심 작업:
  - multi-worker claim model
  - external queue / lock
  - rolling restart / health-based traffic shift
- 완료 조건:
  - Phase 8 종료 기준이 닫힌 뒤에만 HA 검증으로 넘어간다.

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
- Sprint goal: `Phase 8 enabling track를 먼저 닫고, 비선형 엔진 strong closure의 첫 실사용 승격 조각에 들어간다.`
- In scope:
  - `remaining runtime split / read-service long-tail` 마감
  - `durable backend / self-check alert provider policy hardening`
  - planner / recovery / diagnosis 중 최소 하나의 graph/subgraph primary-candidate 승격
  - planner / reviewer 후보 vector retrieval rollout
  - self-growing 장기 효과를 다음 전략 입력으로 연결
  - operator-facing graph/subgraph visualization baseline 설계/첫 슬라이스
  - `orchestrator.py` 구조 리스크 축소의 다음 작은 슬라이스 정의
  - current state gap report / phase 문서 / handoff 동기화
  - production-readiness 기본선 유지
  - 회귀 테스트 유지
- Out of scope:
  - Phase 9 `Zero-Downtime / HA`
  - `full visual editor / drag-and-drop` 급 graph workflow 편집기
  - 운영 시크릿 실제 로테이션과 파괴적 Git 히스토리 정리

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
  - Phase 7 baseline은 거의 닫혔지만 remaining runtime split, durable backend, self-check alert provider policy는 아직 남아 있고 이제 Phase 8 enabling track으로 관리해야 함
  - Phase 4 기능의 shadow/opt-in 비중이 아직 높아 Phase 8 strong closure가 필요함
  - self-growing bridge는 장기 추세 집계와 failure cluster 연결, regressed/insufficient baseline facet, recurring failure cluster 재발 감소 측정까지 올라왔지만 장기 운영 증명은 아직 더 필요함
- 현재 기준 대형 파일 상태:
  - [app/dashboard.py](../app/dashboard.py): `452` lines
  - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 즉, `dashboard.py`는 route와 builder 본문을 [dashboard_job_router](../app/dashboard_job_router.py), [dashboard_write_router](../app/dashboard_write_router.py), [dashboard_operator_router](../app/dashboard_operator_router.py), [dashboard_config_router](../app/dashboard_config_router.py), [dashboard_builder_runtime](../app/dashboard_builder_runtime.py) 로 분리한 뒤 compatibility wrapper만 남긴 상태까지 내려왔고, `orchestrator.py`도 failure transition/runtime 분리와 worker restart safety baseline까지 올라왔다. 다만 핵심 구조 리스크는 여전히 orchestrator 쪽에 남아 있다.
- 상세 판정은 [CURRENT_STATE_GAP_REPORT.md](./CURRENT_STATE_GAP_REPORT.md) 를 따른다.

## Next Priority Shift
- 현재부터의 우선순위는 `새 기능 폭 확대`보다 아래 순서를 따른다.
  1. `Phase 8-E carry-over enabling track`
  2. `8-A Nonlinear Runtime Promotion`
  3. `8-B Vector Retrieval Promotion`
  4. `8-C Self-Growing Strong Closure`
  5. `8-D Graph / Subgraph Visibility Baseline`
- 현재 가장 자연스러운 다음 후보:
  - `remaining runtime split / read-service long-tail` 정리
  - `durable backend / self-check alert provider policy hardening`
  - 이후 `planner/reviewer vector retrieval`과 `planner/recovery/diagnosis graph promotion`
- 현재 판단상 provider burst 계측, cooldown 전이, provider quarantine, provider circuit-breaker, planner/reviewer alternate route fallback, worker startup sweep trace, restart-safe requeue reason, running node/job mismatch audit, dead-letter 재큐잉 액션, operator note trail, dead-letter list / recovery history summary, provider outage history, startup sweep history, dead-letter / recovery action drilldown, recovery action groups, operator action trail까지는 올라왔다. `workspace_repository_runtime`, `preview_runtime`, `app_type_runtime`, `issue_spec_runtime`, `product_definition_runtime`, `product_review_runtime`, `artifact_io_runtime`, `design_governance_runtime`, `improvement_runtime`, `memory_retrieval_runtime`, `memory_quality_runtime`, `structured_memory_runtime`, `ux_review_runtime`, `template_artifact_runtime`, `tool_support_runtime`, `workflow_binding_runtime`, `job_execution_runtime`, `dashboard_job_action_runtime`, `dashboard_job_detail_runtime`, `dashboard_job_list_runtime`, `dashboard_job_workflow_runtime`, `dashboard_job_artifact_runtime`, `dashboard_view_runtime`, `dashboard_job_enqueue_runtime`, `dashboard_github_cli_runtime`, `dashboard_assistant_diagnosis_runtime`, `dashboard_app_registry_runtime`, `dashboard_settings_runtime`, `dashboard_assistant_runtime`, `dashboard_memory_admin_runtime`, `dashboard_issue_registration_runtime`, `dashboard_compat_runtime`, `dashboard_builder_runtime`, `dashboard_operator_router`, `dashboard_config_router`, `dashboard_write_router`, `dashboard_job_router`, `patch_control_runtime`, `dashboard_patch_runtime`, `patch_service_runtime`, `patch_health_runtime`, `patch_backup_runtime`, `durable_runtime_hygiene`, `durable_runtime_self_check`, `security_governance_runtime`, `self_check_alert_delivery_runtime`까지 분리됐고, `summary_runtime`은 commit stage 본문을, `fixed_pipeline_runtime`은 legacy fixed pipeline 본문을 흡수했다. product-review operating principle alignment도 runtime 쪽으로 흡수됐다. self-growing bridge도 `_docs/SELF_GROWING_EFFECTIVENESS.json` baseline, 최근 7일 추세, 앱별 효과 분포, `failure_pattern_cluster` 기반 follow-up 효과, 재발 감소/유지/증가 집계, regressed reason 분포, insufficient baseline 원인 분포, 최근 회귀/기준 부족 사례까지 올라왔다. Phase 7은 baseline을 거의 닫았고, 남은 조각은 이제 Phase 8 enabling track의 blocker 정리로 본다. 다음 조각은 나머지 read-heavy long-tail 정리, self-check alert provider policy hardening, 이후 planner/reviewer vector retrieval rollout과 graph/subgraph promotion이다.
