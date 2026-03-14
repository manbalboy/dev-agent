# AgentHub 기능 상세 요약

주의:
- 이 문서는 전체 구조를 빠르게 훑는 `snapshot` 성격입니다.
- 최신 운영/설치 기준은 [README.md](./README.md)를 우선합니다.
- 현재 로드맵과 phase 상태는 [docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](./docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md) 및 각 `docs/PHASE*.md`를 우선합니다.
- 목표 도달 관점 우선순위는 [docs/GOAL_CLOSURE_PRIORITY_RESET.md](./docs/GOAL_CLOSURE_PRIORITY_RESET.md)를 우선합니다.

## 1. 프로젝트 개요
AgentHub는 GitHub 이슈 라벨 이벤트(`agent:run`)를 받아, 고정된 오케스트레이션 파이프라인으로 작업을 수행하고 PR 생성까지 자동화하는 FastAPI 기반 시스템입니다.

핵심 원칙:
- 순서와 재시도 정책은 워커 코드가 결정
- 현재 기본 실행기는 `Gemini + Codex`이며, 레거시 `Claude/Copilot` 이름은 일부 호환 경로에만 남을 수 있음
- 작업 상태/로그/결과를 JSON 스토어와 대시보드로 가시화

---

## 2. 아키텍처 구성
- API 서버: `app/main.py` (FastAPI)
- 워커: `app/worker_main.py`
- 오케스트레이터: `app/orchestrator.py`
- 추출된 런타임:
  - `app/assistant_runtime.py`
  - `app/agent_config_runtime.py`
  - `app/summary_runtime.py`
  - `app/content_stage_runtime.py`
  - `app/review_fix_runtime.py`
  - `app/planner_runtime.py`
  - `app/implement_runtime.py`
  - `app/workflow_node_runtime.py`
  - `app/workflow_pipeline_runtime.py`
  - `app/provider_runtime.py`
  - `app/preview_runtime.py`
  - `app/app_type_runtime.py`
  - `app/product_definition_runtime.py`
  - `app/improvement_runtime.py`
  - `app/memory_retrieval_runtime.py`
  - `app/memory_quality_runtime.py`
  - `app/structured_memory_runtime.py`
  - `app/integration_recommendation_runtime.py`
  - `app/integration_guide_runtime.py`
  - `app/ux_review_runtime.py`
  - `app/tool_support_runtime.py`
  - `app/fixed_pipeline_runtime.py`
  - `app/template_artifact_runtime.py`
  - `app/workspace_repository_runtime.py`
  - `app/workflow_resolution_runtime.py`
  - `app/docs_snapshot_runtime.py`
  - `app/dashboard_job_runtime.py`
  - `app/dashboard_roles_runtime.py`
  - `app/dashboard_runtime_input_runtime.py`
  - `app/dashboard_integration_registry_runtime.py`
  - `app/dashboard_admin_metrics_runtime.py`
  - `app/runtime_recovery_trace.py`
  - `app/failure_classification.py`
  - `app/provider_failure_counter_runtime.py`
  - `app/design_governance_runtime.py`
- 저장소: `app/store.py` (`jobs.json`, `queue.json`)
- 웹훅 처리: `app/github_webhook.py`
- 대시보드/API: `app/dashboard.py`
- 명령 템플릿 실행기: `app/command_runner.py`

현재 대형 파일 상태:
  - `app/dashboard.py`: `3821` lines
  - `app/orchestrator.py`: `2605` lines
- 따라서 구조 개선은 진행 중이지만 아직 완료 단계는 아님

운영 신뢰성 측면에서는 `failure classification`, `class-aware retry policy`, `needs_human structured handoff`, `provider failure counters`, `provider cooldown window`, `provider_quarantined` baseline, `provider_circuit_open` baseline, `planner/reviewer alternate route fallback` baseline, `worker startup sweep trace` baseline, `restart-safe requeue reason` baseline, `running node/job mismatch audit` baseline, `dead_letter` baseline, `dead-letter 재큐잉 액션`, `dead-letter operator note trail`, `dead-letter list / recovery history summary`, `provider outage history`, `startup sweep history`, `dead-letter / recovery action drilldown`, `recovery action groups`, `operator action trail`까지 들어갔습니다. 현재는 표준 재시도 루프와 `hard gate`가 workspace 단위 provider 카운터를 남기고, 반복 `provider_timeout/tool_failure`는 먼저 `cooldown_wait`, 더 심한 burst는 `provider_quarantined`, 더 길게 누적되면 `provider_circuit_open`으로 전이됩니다. planner/reviewer는 Gemini burst가 workspace 기준으로 격리되거나 circuit-open 상태면 Codex fallback 템플릿을 우선 선택합니다. worker 시작 시에는 orphan/running/queued 정리 결과와 running job/node_run mismatch audit 결과가 `worker_startup_sweep_trace.json`에 남고, stale auto-recovery / dead-letter retry / manual workflow retry는 공통 `requeue_reason_summary`로 남습니다. admin 운영 지표에서는 이제 dead-letter 목록, 최근 recovery trail, provider outage history, startup sweep history를 직접 확인하고 상태별로 필터링할 수 있으며, recovery action group과 operator action trail도 같이 봅니다. 구조 리팩터 쪽에서는 preview/deploy helper, app type 판별과 non-web UX skip helper, issue/spec stage helper, product-definition stage/fallback/contract helper, product-review score/evidence/trend helper와 operating principle alignment helper, improvement stage/strategy helper, artifact I/O helper, design-system decision/stage-contract/pipeline-analysis helper, memory retrieval/context/shadow/ingest helper, memory quality/feedback/ranking helper, structured memory/convention helper, UX review/screenshot helper, log/heartbeat helper, stop-signal/agent-profile/job lookup helper, track/escalation/recovery toggle helper, template variable/fallback artifact helper, tool/search/evidence helper, commit stage/helper 본문, legacy fixed pipeline 본문, workflow binding/context helper, job dispatch/single-attempt helper, repository/stage support helper, orchestrator context/helper bridge, dashboard job action/service helper, patch status helper, patch run progress helper, patch updater heartbeat/claim baseline helper, patch service drain/restart helper, patch health helper, patch rollback helper, patch backup helper가 각각 `preview_runtime`, `app_type_runtime`, `issue_spec_runtime`, `product_definition_runtime`, `product_review_runtime`, `artifact_io_runtime`, `design_governance_runtime`, `improvement_runtime`, `memory_retrieval_runtime`, `memory_quality_runtime`, `structured_memory_runtime`, `ux_review_runtime`, `job_log_runtime`, `job_control_runtime`, `job_mode_runtime`, `template_artifact_runtime`, `tool_support_runtime`, `summary_runtime`, `fixed_pipeline_runtime`, `workflow_binding_runtime`, `job_execution_runtime`, `repository_stage_runtime`, `orchestrator_context_runtime`, `dashboard_job_action_runtime`, `patch_control_runtime`, `dashboard_patch_runtime`, `patch_updater_runtime`, `patch_service_runtime`, `patch_health_runtime`, `patch_rollback_runtime`, `patch_backup_runtime`으로 흡수됐고, Phase 6의 operator control plane / third-party integration registry도 `missing integration input reason surface`, `env bridge policy hardening`, `planner recommendation draft`, `operator approve/reject action`, `approval trail`, `prompt-safe guide summary`, `code pattern/snippet hint`, `verification checklist injection`, `failed job operator approval boundary`, `integration usage trail`, `missing-input / auth / quota facet`, `integration health summary`까지 들어갔습니다. 운영 화면은 이제 통합 항목별 required env가 `제공됨 / 요청됨 / 미연결` 중 무엇인지와 `준비 완료 / 승인 대기 / 입력 요청됨 / 운영자 입력 필요 / 보류됨` 상태를 바로 조회하고, admin metrics에서는 승인 상태/준비 상태/최근 사용 통합/차단 경계/자주 막히는 env/최근 막힌 작업까지 요약해서 봅니다. job detail에서는 `integration_operator_boundary`, `integration_usage_trail`, `integration_health_facets`를 함께 보게 됩니다. integration-linked env는 approval/input readiness가 맞지 않으면 runtime env bridge에 들어가지 않고 `blocked_inputs`로 남습니다. planner recommendation은 이제 `operator_rejected` 통합을 구현 후보에서 제외하고, planner/coder/reviewer는 승인된 통합만 `_docs/INTEGRATION_GUIDE_SUMMARY.md`, `_docs/INTEGRATION_CODE_PATTERNS.md`, `_docs/INTEGRATION_VERIFICATION_CHECKLIST.md`로 안전하게 주입받습니다. self-growing bridge 쪽도 이제 `_docs/SELF_GROWING_EFFECTIVENESS.json` artifact와 admin 운영 지표 집계가 들어가서, follow-up job이 부모 대비 `개선됨 / 변화 없음 / 회귀됨 / 비교 기준 부족` 중 무엇인지뿐 아니라 최근 7일 추세, 앱별 개선/회귀 분포, `failure_pattern_cluster` 기반 follow-up 효과, 재발 감소/유지/증가 집계, 회귀 원인 분포, 기준 부족 원인 분포, 최근 회귀/기준 부족 사례까지 한 화면에서 볼 수 있습니다. Phase 7에서는 admin 화면에서 현재 branch/commit/upstream/behind/ahead/dirty/update available을 읽는 `패치 상태` baseline, patch run 상태/단계/진행률/운영자 메모를 읽는 `패치 실행 진행률` baseline, 별도 `Updater 서비스` heartbeat/claim baseline, patch lock 기반 새 작업 수락 차단, updater-driven `worker stop -> api restart -> worker restart` drain/restart baseline, 재기동 후 API/worker/queue 상태를 확인하는 `post-update health check` baseline, 실패 patch run을 `rollback_requested -> rolling_back -> rollback_verifying -> rolled_back/rollback_failed`로 처리하는 `rollback baseline`, 서비스 재기동 직전 핵심 상태를 `data/patch_backups/<backup_id>/manifest.json`으로 남기는 `pre-patch backup` baseline, operator-triggered `restore_requested -> restoring -> restore_verifying -> restored/restore_failed`와 backup manifest verification baseline까지 들어갔습니다. 다음 전략 단계는 `7-E1 durable runtime / workspace hygiene`, `enterprise 운영 계층 보강`, `dashboard write action/service 잔여 축소`입니다.
앱 분류 작업 기준도 강화됐습니다. 이제 planner/coder/reviewer prompt는 React Native/Expo 중심의 모바일 앱 규칙을 기본 반영하고, emulator target, RN 테스트 기준, mobile secret 처리 원칙은 [docs/MOBILE_APP_DEVELOPMENT_MODE_RULESET.md](./docs/MOBILE_APP_DEVELOPMENT_MODE_RULESET.md)를 따릅니다.

데이터 저장 위치:
- 작업 목록: `data/jobs.json`
- 큐: `data/queue.json`
- 로그: `data/logs/*.log`

---

## 3. 워크플로우(실행 파이프라인)
한 Job은 아래 순서로 실행됩니다.
1. `prepare_repo`
2. `read_issue`
3. `write_spec`
4. `idea_to_product_brief`
5. `generate_user_flows`
6. `define_mvp_scope`
7. `architecture_planning`
8. `project_scaffolding`
9. `plan_with_gemini`
10. `design_with_codex`
11. `publish_with_codex`
12. `copywriter_with_codex`
13. `documentation_with_claude`
14. `implement_with_codex`
15. `code_change_summary`
16. `test_after_implement`
17. `commit_implement`
18. `review_with_gemini`
19. `product_review`
20. `improvement_stage`
21. `fix_with_codex`
22. `test_after_fix`
23. `commit_fix`
24. `push_branch`
25. `create_pr`
26. `finalize`

재시도 정책:
- 실패 시 최대 `AGENTHUB_MAX_RETRIES`(기본 3) 재시도
- 최종 실패 시 `failed` 처리 + 상태/로그 기록
- 필요 시 WIP PR 생성 시도

---

## 4. 웹훅/이슈 트리거 기능
### 4.1 GitHub 웹훅 수신
- 엔드포인트: `POST /webhooks/github`
- 이벤트: `issues` + `labeled`
- HMAC 서명(`X-Hub-Signature-256`) 검증
- `agent:run` 라벨 조건 충족 시 Job 생성

### 4.2 대시보드 직접 이슈 등록
- 엔드포인트: `POST /api/issues/register`
- 동작:
  - GitHub 이슈 생성
  - 라벨 자동 부착: `agent:run`, `app:{app_code}`, `track:{track}`
  - Job 생성 + 큐 적재

중복 방지:
- 같은 이슈에 `queued/running` Job이 이미 있으면 신규 생성 대신 기존 Job 연결

---

## 5. 앱 분리(App Namespace) 기능
### 5.1 앱 등록 관리
- `GET /api/apps`: 앱 목록 조회
- `POST /api/apps`: 앱 등록/수정
- `DELETE /api/apps/{app_code}`: 앱 삭제
- 설정 파일: `config/apps.json`

### 5.2 앱/트랙 메타
Job에 다음 메타가 저장됩니다.
- `app_code` (예: `mvp-1`)
- `track` (`new`, `enhance`, `bug`)

### 5.3 네이밍 분리
- 브랜치: `agenthub/{app_code}/issue-{number}-{jobid8}`
- 로그 파일: `{app_code}--{job_id}.log`
- 워크스페이스 경로: `workspaces/{app_code}/{owner__repo}`

---

## 6. 대시보드 UI 고도화 상태
### 6.1 메인
- 메뉴 분리: `작업 현황` / `운영 설정` / `AI 진단` / `운영 지표`
- 리스트 실시간 갱신(`GET /api/jobs` polling)
- 상태 카드(전체/대기/실행/완료/실패)
- KST 포맷 시각 표시
- 모바일 카드형 목록 렌더링

### 6.2 설정 내부 메뉴 분리
- `이슈 등록`
- `앱 관리`
- `에이전트`
- 아이콘+라벨 메뉴 UI
- 모바일 반응형 대응

### 6.3 앱 관리 UI
- 등록 앱 목록 테이블(`앱명/코드/저장소/삭제`)
- 삭제 버튼 아이콘화(휴지통)

### 6.4 잡 상세
- 실시간 터미널 스타일 로그
- 최신 로그 상단 정렬
- 작업 단계/시도 그룹 하이라이트
- 행위자 라벨 표시 (`ORCHESTRATOR`, `CODER`, `PLANNER`, `REVIEWER`, `GITHUB`, `GIT`, `SYSTEM`, `SHELL`)
- 에러/경고 분류 요약
- 핵심 요약 카드
- workflow/lineage/operator input/diagnosis trace 보드

### 6.5 테마
- 다크/라이트 모드 토글
- 모드 상태 로컬 저장

### 6.6 운영 보조 기능
- 운영자 입력 레지스트리와 draft 추천
- assistant chat/log-analysis diagnosis trace
- admin diagnosis trace 비교/상세 drilldown
- 메모리 관리 및 backlog 후보 운영 UI

---

## 7. 에이전트/명령 템플릿 관리
### 7.1 템플릿 설정 API
- `GET /api/agents/config`
- `POST /api/agents/config`

### 7.2 기능
- 쉬운 입력 모드 + 상세 문자열 모드
- Planner/Coder/Reviewer/Escalation 에이전트 분리 설정
- Escalation 활성화 토글 (`AGENTHUB_ENABLE_ESCALATION`)
- CLI 연결 확인: `GET /api/agents/check`
  - `gemini`, `codex`, `git`, `gh` 실행 가능 여부를 함께 점검
- 적용 모델 확인: `GET /api/agents/models`
- 위험 플래그 감지/제거 보조

### 7.3 현재 기본 커맨드 상태
- `planner/reviewer`: Gemini
- `coder`: Codex
- `documentation`: Codex
- `commit_summary / pr_summary / escalation`: Gemini judgement route
- `tester`: bash 실행
- `test_reviewer`: Gemini baseline route
- helper fallback: Codex helper
- 기본 예시 파일: `config/ai_commands.example.json`
- 실제 로컬 런타임 파일: `config/ai_commands.json` (운영자 환경별 상이할 수 있음)
- 역할 경계 기준 문서: [docs/AI_ROLE_EXECUTION_POLICY.md](./docs/AI_ROLE_EXECUTION_POLICY.md)

---

## 8. 오류 분류/로그 정책
- `STDERR` 문자열 자체만으로 즉시 치명 오류 처리하지 않도록 완화
- 실제 실패 조건(종료코드/파이프라인 실패) 중심으로 최종 ERROR 판단
- 실패 원인 메시지에 다음 액션 가이드 포함
- `TECH_WRITER`, `PR_SUMMARY`, `CODEX_HELPER` 같은 선택적 helper route 실패는 운영 화면에서 `보조 실패`로 강등해 본체 실패와 분리
- `verify CLI login/state`, `authentication`, `quota` 계열 문구는 `CLI 로그인/인증 상태 확인 필요`, `사용량/쿼터 확인 필요` 힌트로 분리
- job detail의 로그 운영 요약은 이제 `핵심 오류`, `보조 오류`, `인증 힌트`를 각각 따로 보여줌

---

## 9. 운영/유틸 스크립트
- `scripts/setup_local_config.sh`
  - `.env`, `config/ai_commands.json`, `config/apps.json` 생성
- `scripts/install_systemd.sh`
  - API/Worker systemd 등록
- `scripts/test_live_webhook.sh`
  - 실환경 웹훅 플로우 검증
- `scripts/workspace_app.sh` (신규)
  - 앱 작업물 실행 관리
  - `start/stop/status`
  - 앱 코드별 포트 `3100~3199` 자동 할당/고정 (`config/app_ports.json`)
  - 실행 모드:
    - `web`
    - `expo-android`
    - `expo-ios`
  - `rn-android`
  - `rn-ios`
  - mobile mode는 포트 대신 emulator/simulator 실행 명령과 상태를 관리
  - 앱 분류 저장소는 테스트 단계 후 `_docs/MOBILE_APP_CHECKLIST.md`에 마지막 mobile 검증 요약을 남김
  - admin 운영 지표에서 최근 앱 실행 모드, 상태, 명령을 읽을 수 있음
- `scripts/mobile_e2e_runner.sh` (신규)
  - Android emulator / iOS simulator를 재사용 또는 부팅
  - mobile E2E 명령을 platform별로 선택
  - 결과를 `_docs/MOBILE_E2E_RESULT.json`에 기록
  - job detail workflow 탭과 admin 운영 지표에서도 마지막 모바일 E2E 결과를 직접 조회
- `scripts/run_agenthub_tests.sh`
  - `mobile-e2e-android`, `mobile-e2e-ios` 모드 지원
  - `e2e` 모드에서 mobile E2E script가 있으면 Android 우선으로 자동 선택

---

## 10. 주요 API 목록 요약
- `GET /` 대시보드
- `GET /api/jobs` 작업 리스트
- `GET /api/jobs/{job_id}` 작업 상세
- `GET /logs/{file_name}` 로그 파일
- `POST /webhooks/github` GitHub 웹훅
- `POST /api/issues/register` 대시보드 이슈 등록+트리거
- `GET /api/apps` 앱 목록
- `POST /api/apps` 앱 등록/수정
- `DELETE /api/apps/{app_code}` 앱 삭제
- `GET /api/agents/config` 템플릿 조회
- `POST /api/agents/config` 템플릿 저장
- `GET /api/agents/check` CLI 연결 확인
- `GET /api/agents/models` 모델 확인
- `GET /healthz` 헬스체크

---

## 12. 워크플로우 노드 엔진 (Phase 2 전환 완료)
### 12.1 실행 제어 로직
- **유연한 노드 실행**: 고정 파이프라인 대신 JSON 정의 기반의 워크플로우 실행 엔진 도입
- **에지 기반 전이**: 노드 실행 결과(`success`, `failure`, `always`)에 따른 조건부 경로 제어
- **우선순위 기반 선택**: `Job > App > Default` 순의 워크플로우 결정 로직 (`docs/JOB_WORKFLOW_RESOLUTION.md`)
- **자동 폴백**: 워크플로우 로딩/검증 실패 시 기존 고정 파이프라인으로 안전하게 전환

### 12.2 노드 타입 및 확장성
- **Executor Registry**: 노드 타입별 핸들러를 매핑하여 오케스트레이터 코드 수정 없이 기능 확장 가능 (`app/workflow_registry.py`)
- **특수 제어 노드**: `if_label_match`(라벨 기반 분기), `loop_until_pass`(성공할 때까지 루프) 지원

### 12.3 상태 추적 및 가시성
- **노드 단위 실행 이력**: 모든 노드의 시작/종료/상태/에러를 별도 기록 (`docs/NODE_RUNS_SCHEMA.md`)
- **대시보드 연동**: 
  - Job 상세 화면에서 노드별 실행 상태 및 타임라인 확인
  - 워크플로우 에디터를 통한 시각적 구조 확인 및 편집

---

## 13. 현재 냉정한 상태 판단
- 현재 시스템은 `강한 기반을 가진 고급 프로토타입`이다.
- 방향은 맞지만 아직 `스스로 성장하는 24시간 개발 동료`라고 보기엔 이르다.
- 가장 큰 부족분:
  - `app/orchestrator.py` 구조 리스크
  - enterprise 운영 계층과 durable backend 미완
  - shadow/opt-in 기능의 primary 전환 부족
  - dashboard write action/service 경계가 아직 두껍다

---

## 14. 현재 운영 시 유의사항
- GitHub 이슈/PR 생성은 서버의 `gh auth`와 외부 네트워크(`api.github.com`) 상태에 의존
- npm 설치/외부 패키지 설치는 DNS/아웃바운드 정책에 영향받음
- 테스트용 앱 실행은 `workspace_app.sh`로 3100번대 포트 분리 권장
