# Current Handoff

기준 시각: 2026-03-13 (KST)

## 1. 이번 턴까지 완료한 것

### Mobile E2E Runner Baseline

- [scripts/mobile_e2e_runner.sh](../scripts/mobile_e2e_runner.sh) 를 추가했다.
  - `--platform android|ios`
  - Android emulator 재사용/부팅
  - iOS simulator 재사용/부팅
  - platform별 mobile E2E 명령 선택
  - `_docs/MOBILE_E2E_RESULT.json` 기록
- [scripts/run_agenthub_tests.sh](../scripts/run_agenthub_tests.sh) 는 이제 아래 모드를 지원한다.
  - `mobile-e2e-android`
  - `mobile-e2e-ios`
  - `e2e` 모드에서 mobile E2E script가 있으면 Android 우선 자동 선택
- [app/workflow_resume.py](../app/workflow_resume.py) 에 `mobile_e2e_result` artifact path를 추가했다.
- [app/mobile_quality_runtime.py](../app/mobile_quality_runtime.py) 는 이제 `_docs/MOBILE_E2E_RESULT.json` 을 읽어 `_docs/MOBILE_APP_CHECKLIST.md`에 마지막 mobile E2E 결과를 함께 요약한다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_mobile_e2e_runner_script.py](../tests/test_mobile_e2e_runner_script.py)
  - [test_run_agenthub_tests_script.py](../tests/test_run_agenthub_tests_script.py)
  - [test_mobile_quality_runtime.py](../tests/test_mobile_quality_runtime.py)

### Mobile E2E Dashboard Surface

- [app/dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 이제 작업 workspace의 `_docs/MOBILE_E2E_RESULT.json` 을 읽어 `mobile_e2e_result` payload를 만든다.
- [app/dashboard.py](../app/dashboard.py) job detail API는 `mobile_e2e_result`를 반환한다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) workflow 탭에는 `모바일 E2E 결과` 보드가 추가됐다.
  - 상태
  - platform
  - runner
  - target name / target id
  - command
  - notes
  - artifact path
- [app/dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 app runner 메타와 함께 mobile E2E artifact도 읽어 `runtime.app_runner_status.mobile_e2e_*` 집계를 반환한다.
- [app/templates/index.html](../app/templates/index.html) 운영 지표 `앱 실행 상태` 카드에는 아래가 추가됐다.
  - 모바일 E2E 기록 수
  - status 분포
  - 최근 모바일 E2E 결과 목록
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 6-A1 integration registry schema + storage baseline

- [app/models.py](../app/models.py) 에 [IntegrationRegistryRecord](../app/models.py) 를 추가했다.
- [app/store.py](../app/store.py) 는 이제 integration registry entry를 JSON/SQLite 둘 다 저장/조회할 수 있다.
  - `upsert_integration_registry_entry()`
  - `get_integration_registry_entry()`
  - `list_integration_registry_entries()`
- [app/dashboard_integration_registry_runtime.py](../app/dashboard_integration_registry_runtime.py) 를 추가했다.
  - `integration_id`, `supported_app_types`, `env key`, tags 정규화
  - list filter
  - save/upsert serialization
- [app/dashboard.py](../app/dashboard.py) 에 아래 admin API baseline을 추가했다.
  - `GET /api/admin/integrations`
  - `POST /api/admin/integrations`
- 현재는 UI 없이 API baseline만 들어간 상태고, 다음 슬라이스가 list/read UI다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_store_and_queue.py](../tests/test_store_and_queue.py)
  - [test_dashboard_integration_registry_runtime.py](../tests/test_dashboard_integration_registry_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 6-A2 integration registry admin read/list UI

- [app/templates/index.html](../app/templates/index.html) `운영 입력/상태` 섹션에 `서드파티 통합 레지스트리` 조회 전용 보드를 추가했다.
- 이번 슬라이스는 아래만 포함한다.
  - 검색어 / 카테고리 / 앱 유형 / 사용 여부 필터
  - 통합 항목 목록
  - 선택한 통합의 상세 정보
  - operator guide / implementation guide / verification notes 읽기 전용 노출
- 등록/수정 UI는 아직 넣지 않았다. 이번 단계는 `읽기 전용 operator surface`만 닫았다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 6-B1 integration -> required runtime input link

- [app/dashboard_integration_registry_runtime.py](../app/dashboard_integration_registry_runtime.py) 가 이제 integration entry마다 `required_input_summary`, `required_input_links`를 계산한다.
- 링크 기준은 `required_env_keys`와 runtime input request의 `env_var_name` 매칭이다.
- 각 필수 env는 현재 아래 중 하나로 보인다.
  - `provided`
  - `requested`
  - `missing`
- [app/templates/index.html](../app/templates/index.html) 의 `서드파티 통합 레지스트리` 상세 보드는 이제 필수 env별 연결 상태와 최근 요청을 같이 보여준다.
- 이번 슬라이스는 linkage/read-only 까지만 닫았다. missing request 생성이나 needs_human surface는 다음 슬라이스다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_dashboard_integration_registry_runtime.py](../tests/test_dashboard_integration_registry_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 6-B2 missing integration input reason surface

- [app/dashboard_integration_registry_runtime.py](../app/dashboard_integration_registry_runtime.py) 가 이제 integration entry마다 아래 operator-facing readiness field를 같이 계산한다.
  - `input_readiness_status`
  - `input_readiness_reason`
- 현재 readiness는 아래 네 단계로 정규화된다.
  - `ready`
  - `approval_required`
  - `input_requested`
  - `input_required`
- [app/templates/index.html](../app/templates/index.html) 의 `서드파티 통합 레지스트리` 목록/상세 보드는 이제 숫자 요약만이 아니라
  - `준비 완료`
  - `승인 대기`
  - `입력 요청됨`
  - `운영자 입력 필요`
  배지와 사유 문구를 직접 보여준다.
- 이번 슬라이스로 `통합은 등록됐지만 필요한 입력이 비어 있다`는 상태가 운영 화면에서 명시적으로 보이게 됐다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_dashboard_integration_registry_runtime.py](../tests/test_dashboard_integration_registry_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 6-C1 planner recommendation draft

- [app/integration_recommendation_runtime.py](../app/integration_recommendation_runtime.py) 를 추가했다.
- planner는 이제 실행 전 `_docs/INTEGRATION_RECOMMENDATIONS.json` 을 생성하고, 통합 항목을 `도입 검토 후보`로만 추천한다.
- recommendation payload는 아래를 같이 남긴다.
  - `required_input_summary`
  - `input_readiness_status`
  - `input_readiness_reason`
  - `recommendation_status`
  - `matched_keywords`
  - `reason`
- [app/planner_runtime.py](../app/planner_runtime.py), [app/prompt_builder.py](../app/prompt_builder.py), [app/orchestrator.py](../app/orchestrator.py), [app/workflow_resume.py](../app/workflow_resume.py) 도 같이 맞췄다.
- planner prompt에는 `INTEGRATION_RECOMMENDATIONS.json` 을 `도입 검토 후보`로만 다루고 approval 전 자동 도입을 금지하는 규칙이 추가됐다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_integration_recommendation_runtime.py](../tests/test_integration_recommendation_runtime.py)
  - [test_planner_runtime.py](../tests/test_planner_runtime.py)

### Phase 6-C2 operator approve/reject action

- [app/models.py](../app/models.py), [app/store.py](../app/store.py) 의 integration registry entry는 이제 아래 approval 메타를 저장한다.
  - `approval_status`
  - `approval_note`
  - `approval_updated_at`
  - `approval_updated_by`
- [app/dashboard_integration_registry_runtime.py](../app/dashboard_integration_registry_runtime.py) 는 이제 operator action을 처리한다.
  - `approve`
  - `reject`
  - `reset`
- integration readiness는 이제 아래 상태까지 정규화된다.
  - `ready`
  - `approval_required`
  - `approval_rejected`
  - `input_requested`
  - `input_required`
- [app/dashboard.py](../app/dashboard.py) 에 아래 admin API를 추가했다.
  - `POST /api/admin/integrations/{integration_id}/approval`
- [app/templates/index.html](../app/templates/index.html) 의 `서드파티 통합 레지스트리` 상세 보드는 이제 아래를 직접 보여준다.
  - 승인 상태 배지
  - 최근 조치자 / 최근 조치 시각
  - 운영자 메모
  - `승인 / 다시 검토 / 보류` 액션
- [app/integration_recommendation_runtime.py](../app/integration_recommendation_runtime.py) 는 recommendation payload에 approval 상태를 같이 남긴다.
  - rejected candidate는 `operator_rejected`
  - approved+ready candidate는 `approved_candidate`
- [app/prompt_builder.py](../app/prompt_builder.py) 는 이제 `operator_rejected` 통합을 구현 후보로 제안하지 않도록 규칙을 강화했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_store_and_queue.py](../tests/test_store_and_queue.py)
  - [test_dashboard_integration_registry_runtime.py](../tests/test_dashboard_integration_registry_runtime.py)
  - [test_integration_recommendation_runtime.py](../tests/test_integration_recommendation_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 6-C3 approval trail

- [app/models.py](../app/models.py), [app/store.py](../app/store.py) 의 integration registry entry는 이제 append-only `approval_trail`을 저장한다.
- [app/dashboard_integration_registry_runtime.py](../app/dashboard_integration_registry_runtime.py) 는 승인 액션마다 아래 정보를 trail에 append한다.
  - `action`
  - `source`
  - `previous_status`
  - `current_status`
  - `note`
  - `acted_by`
  - `acted_at`
- [app/templates/index.html](../app/templates/index.html) 의 `서드파티 통합 레지스트리` 상세 보드는 이제 최근 승인/보류/재검토 이력을 직접 보여준다.
- [app/integration_recommendation_runtime.py](../app/integration_recommendation_runtime.py) 의 recommendation payload는 이제 아래도 같이 남긴다.
  - `approval_trail_count`
  - `latest_approval_action`
- 이번 슬라이스는 operator approval trail baseline이고, recommendation과 approval의 교차 usage audit까지는 아직 아니다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_store_and_queue.py](../tests/test_store_and_queue.py)
  - [test_dashboard_integration_registry_runtime.py](../tests/test_dashboard_integration_registry_runtime.py)
  - [test_integration_recommendation_runtime.py](../tests/test_integration_recommendation_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 6-D1 prompt-safe guide summary

- [app/integration_guide_runtime.py](../app/integration_guide_runtime.py) 를 추가했다.
- 승인된 통합만 `_docs/INTEGRATION_GUIDE_SUMMARY.md` 로 요약해 planner/coder/reviewer prompt에 주입한다.
- guide summary에는 아래만 들어간다.
  - `integration_id`, `display_name`, `category`
  - `required_env_keys`
  - `input_readiness_status`, `input_readiness_reason`
  - `approval_status`, `approval_note`, `latest_approval_action`
  - operator / implementation / verification guide compact summary
- secret 값은 포함하지 않고 env var 이름만 남긴다.
- `operator_rejected`, `pending` 통합은 guide summary에서 제외된다.
- 연결 지점:
  - [app/planner_runtime.py](../app/planner_runtime.py)
  - [app/implement_runtime.py](../app/implement_runtime.py)
  - [app/review_fix_runtime.py](../app/review_fix_runtime.py)
  - [app/prompt_builder.py](../app/prompt_builder.py)
  - [app/orchestrator.py](../app/orchestrator.py)
  - [app/workflow_resume.py](../app/workflow_resume.py)
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_integration_guide_runtime.py](../tests/test_integration_guide_runtime.py)
  - [test_planner_runtime.py](../tests/test_planner_runtime.py)
  - [test_implement_runtime.py](../tests/test_implement_runtime.py)
  - [test_review_fix_runtime.py](../tests/test_review_fix_runtime.py)

### Phase 6 문서화 추가

- [PHASE6_OPERATOR_CONTROL_AND_INTEGRATION_REGISTRY_PLAN.md](./PHASE6_OPERATOR_CONTROL_AND_INTEGRATION_REGISTRY_PLAN.md) 를 추가했다.
- 이 문서는 아래를 하나의 phase로 묶는다.
  - 남아 있는 Phase 5 operator control 잔여 항목
  - third-party integration registry
  - runtime input / env bridge upgrade
  - AI recommendation -> operator approval -> implementation 연결
- 특히 `Google Maps` 같은 통합을 운영자가 등록하고, AI는 이를 `검토 후보`로만 제안하며, 승인 후에만 구현/주입되도록 가이드했다.
- 상위 계획과 문서 맵도 새 문서를 source-of-truth 링크로 연결했다.

### 모바일 앱 개발 모드 규칙 추가

- [MOBILE_APP_DEVELOPMENT_MODE_RULESET.md](./MOBILE_APP_DEVELOPMENT_MODE_RULESET.md) 를 추가했다.
- 이 문서에는 아래 기준을 정리했다.
  - app 분류 작업은 React Native 기준
  - greenfield는 Expo managed workflow 우선
  - 기존 bare RN / Expo prebuild 저장소는 현재 구조 보존
  - emulator/simulator 검증 타깃을 라운드 단위로 명시
  - baseline 테스트는 Jest + React Native Testing Library 우선
  - Detox는 기존 저장소가 쓰거나 안정화 단계에서만 우선 고려
  - safe area / keyboard / loading / empty / error / offline 상태를 필수 점검
  - 모바일 secret/API key는 runtime input registry/env bridge로만 연결
- [app/prompt_builder.py](../app/prompt_builder.py) 에 `MOBILE_APP_RULESET_BRIEF` 를 추가하고 planner/coder/reviewer prompt에 주입했다.
- [README.md](../README.md), [DOCUMENT_MAP.md](./DOCUMENT_MAP.md), [AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](./AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md) 에 새 문서를 source-of-truth 링크로 연결했다.
- [scripts/workspace_app.sh](../scripts/workspace_app.sh) 에 아래 실행 프리셋을 추가했다.
  - `web`
  - `expo-android`
  - `expo-ios`
  - `rn-android`
  - `rn-ios`
- mobile mode는 포트 대신 실행 모드/명령/PID/log를 메타 파일로 관리한다.
- 관련 핵심 계약은 [test_workspace_app_script.py](../tests/test_workspace_app_script.py) 로 고정했다.
- [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 `data/pids/app_*.json` 메타를 읽어 앱 실행 상태를 admin metrics `runtime.app_runner_status`로 집계한다.
- [app/templates/index.html](../app/templates/index.html) 운영 지표 `운영 입력/상태` 섹션에는 `앱 실행 상태` 카드가 추가됐다.
  - 실행 메타 수
  - 모바일/웹 모드 수
  - 최근 앱 실행 상태
  - 최근 실행 명령
- [mobile_quality_runtime.py](../app/mobile_quality_runtime.py) 를 추가했고, 테스트 단계가 끝나면 앱 분류 저장소에 `_docs/MOBILE_APP_CHECKLIST.md`를 자동 생성한다.
  - verification target
  - runner mode/state/command
  - 마지막 테스트 증거
  - baseline mobile checklist
- 관련 핵심 계약은 [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py) 로 고정했다.

### preview runtime 추가 분리

- docker preview / port allocation / HTTP probe / PR preview section helper를 [preview_runtime.py](../app/preview_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_deploy_preview_and_smoke_test()`
  - `_detect_container_port()`
  - `_append_preview_section_to_pr_body()`
  - `_build_preview_pr_section()`
  - `_write_preview_markdown()`
  - `_allocate_preview_port()`
  - `_is_local_port_in_use()`
  - `_probe_http()`
- 기존 provider runtime API는 그대로 유지했고, `stage_create_pr()` 가 쓰는 preview callback 계약도 바꾸지 않았다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_preview_runtime.py](../tests/test_preview_runtime.py)
  - [test_provider_runtime.py](../tests/test_provider_runtime.py)

### ux review runtime 추가 분리

- UX screenshot / `UX_REVIEW.md` / SPEC checklist helper를 [ux_review_runtime.py](../app/ux_review_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_stage_ux_e2e_review()`
  - `_capture_ux_screenshots()`
  - `_write_ux_review_markdown()`
  - `_extract_spec_checklist()`
- workflow node 쪽 `owner._stage_ux_e2e_review = fake_ux_stage` monkeypatch 계약은 그대로 유지했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_ux_review_runtime.py](../tests/test_ux_review_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)

### app type runtime 추가 분리

- `SPEC.json` 기반 app type 판별과 non-web UX review skip helper를 [app_type_runtime.py](../app/app_type_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_resolve_app_type()`
  - `_stage_skip_ux_review_for_non_web()`
- workflow node 쪽 `owner._resolve_app_type(...)`, `owner._stage_skip_ux_review_for_non_web(...)` 호출 계약은 그대로 유지했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_app_type_runtime.py](../tests/test_app_type_runtime.py)
  - [test_workflow_node_runtime.py](../tests/test_workflow_node_runtime.py)

### product definition runtime 추가 분리

- product-definition stage/fallback/contract helper를 [product_definition_runtime.py](../app/product_definition_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_run_markdown_generation_with_refinement()`
  - `_stage_idea_to_product_brief()`
  - `_stage_generate_user_flows()`
  - `_stage_define_mvp_scope()`
  - `_stage_architecture_planning()`
  - `_stage_project_scaffolding()`
  - `_build_bootstrap_report()`
  - `_ensure_markdown_stage_contract()`
  - `_missing_markdown_sections()`
  - `_ensure_product_definition_ready()`
  - `_write_*_fallback()` product-definition 계열
- 기존 product-definition generation 테스트와 `_ensure_product_definition_ready()` 계약은 그대로 유지했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_product_definition_runtime.py](../tests/test_product_definition_runtime.py)
  - [test_product_definition_generation.py](../tests/test_product_definition_generation.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)

### improvement runtime 추가 분리

- improvement stage/strategy helper를 [improvement_runtime.py](../app/improvement_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_stage_improvement_stage()`
  - `_build_improvement_strategy_inputs()`
  - `_select_improvement_strategy()`
  - `_select_next_improvement_items()`
- 기존 improvement stage 회귀와 memory/strategy shadow 계약은 그대로 유지했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_improvement_runtime.py](../tests/test_improvement_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)

### memory retrieval runtime 추가 분리

- memory retrieval/context/shadow/ingest helper를 [memory_retrieval_runtime.py](../app/memory_retrieval_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_write_memory_retrieval_artifacts()`
  - `_load_vector_shadow_runtime_entries()`
  - `_write_vector_shadow_index_artifact()`
  - `_load_memory_retrieval_corpus_from_db()`
  - `_load_memory_retrieval_corpus_from_files()`
  - `_write_strategy_shadow_report()`
  - `_ingest_memory_runtime_artifacts()`
  - `_build_strategy_shadow_report_payload()`
  - `_build_route_memory_context()`
  - `_read_json_history_entries()`
- 기존 orchestrator memory/strategy shadow 계약은 그대로 유지했고, 생성 후 `qdrant shadow transport` 교체 monkeypatch도 깨지지 않게 현재 transport를 지연 조회하도록 맞췄다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_memory_retrieval_runtime.py](../tests/test_memory_retrieval_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)

### memory quality runtime 추가 분리

- memory quality/feedback/ranking helper를 [memory_quality_runtime.py](../app/memory_quality_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_write_memory_quality_artifacts()`
  - `_build_memory_feedback_outcome()`
  - `_update_memory_rankings_artifact()`
  - `_memory_ranking_state()`
  - `_memory_kind_from_id()`
- 기존 improvement stage 통합 계약은 그대로 유지했고, memory feedback/rankings artifact shape도 바꾸지 않았다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_memory_quality_runtime.py](../tests/test_memory_quality_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)

### structured memory runtime 추가 분리

- structured memory/convention helper를 [structured_memory_runtime.py](../app/structured_memory_runtime.py) 로 추출했다.
- [orchestrator.py](../app/orchestrator.py) 의 아래 helper는 이제 wrapper만 남기고 runtime 위임 구조로 바뀌었다.
  - `_write_structured_memory_artifacts()`
  - `_update_failure_patterns_artifact()`
  - `_write_conventions_artifact()`
  - `_package_dependency_map()`
  - `_detect_component_extension_preference()`
  - `_detect_test_file_conventions()`
- 기존 improvement stage의 structured memory artifact 계약과 repo convention 추출 계약은 그대로 유지했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_structured_memory_runtime.py](../tests/test_structured_memory_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)

### 로그/로그인 오류 노이즈 완화

- [log_signal_utils.py](../app/log_signal_utils.py) 를 추가해 선택적 helper actor와 CLI 인증/쿼터 힌트를 공통 분류하도록 만들었다.
- [summary_runtime.py](../app/summary_runtime.py) 와 [content_stage_runtime.py](../app/content_stage_runtime.py) 는 이제 optional route 실패를 그대로 길게 노출하지 않고, `CLI 로그인/인증 상태 확인 필요`, `사용량/쿼터 확인 필요` 같은 짧은 힌트로 압축해 fallback 로그를 남긴다.
- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 의 `build_job_log_summary()` 는 이제 아래를 분리해 반환한다.
  - `error_count`: 핵심 오류
  - `optional_error_count`: 보조 helper 실패
  - `auth_hint_count`
  - `latest_optional_error`
  - `latest_auth_hint`
- [app/templates/job_detail.html](../app/templates/job_detail.html) 은 이제 mobile failure summary에서 `TECH_WRITER`, `PR_SUMMARY`, `CODEX_HELPER`, `COPILOT` 같은 선택적 helper의 non-zero 종료를 `오류`가 아니라 `주의`로 보여준다.
- job detail의 `로그 운영 요약` 보드는 이제 `핵심 오류`, `보조 실패`, `인증 힌트`를 따로 노출한다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_dashboard_job_runtime.py](../tests/test_dashboard_job_runtime.py)
  - [test_summary_runtime.py](../tests/test_summary_runtime.py)
  - [test_content_stage_runtime.py](../tests/test_content_stage_runtime.py)

### dashboard runtime 추가 분리

- job detail/runtime signals/log summary/operator input 계산을 [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 로 추출했다.
- [dashboard.py](../app/dashboard.py) 의 `_build_job_runtime_signals()`, `_build_job_lineage()`, `_build_job_log_summary()`, `_build_job_operator_inputs()` 는 wrapper만 남기고 runtime 위임 구조로 바꿨다.
- 관련 핵심 계약은 [test_dashboard_job_runtime.py](../tests/test_dashboard_job_runtime.py) 로 고정했다.
  - non-zero done/error log summary 집계
  - operator input masking/env inventory
- runtime input serialization / draft / request / provide helper를 [dashboard_runtime_input_runtime.py](../app/dashboard_runtime_input_runtime.py) 로 추출했다.
- [dashboard.py](../app/dashboard.py) 의 runtime input admin route는 새 runtime으로 위임만 하도록 정리했다.
- 관련 핵심 계약은 [test_dashboard_runtime_input_runtime.py](../tests/test_dashboard_runtime_input_runtime.py) 로 고정했다.
  - secret masking
  - job context fallback
  - provide 시 status / timestamp 전이
- admin metrics / assistant diagnosis 집계를 [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 로 추출했다.
- [dashboard.py](../app/dashboard.py) 의 `_build_admin_assistant_diagnosis_metrics()`, `_build_admin_metrics()` 는 wrapper만 남기고 runtime 위임 구조로 바꿨다.
- 관련 핵심 계약은 [test_dashboard_admin_metrics_runtime.py](../tests/test_dashboard_admin_metrics_runtime.py) 와 [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py) 로 고정했다.
  - diagnosis trace recent ordering
  - failed tool aggregation
  - admin metrics API payload 계약 유지
- 역할/프리셋 payload 정규화와 CRUD를 [dashboard_roles_runtime.py](../app/dashboard_roles_runtime.py) 로 추출했다.
- [dashboard.py](../app/dashboard.py) 의 `/api/roles`, `/api/role-presets` route는 이제 runtime 위임만 하도록 정리했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_dashboard_roles_runtime.py](../tests/test_dashboard_roles_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 5-A1 runtime recovery trace

- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) 를 추가해 `_docs/RUNTIME_RECOVERY_TRACE.json` append contract를 만들었다.
- [worker_main.py](../app/worker_main.py) 의 stale running auto-recovery는 이제 `stale_heartbeat` reason code와 `requeue / needs_human` decision을 trace artifact에 남긴다.
- [recovery_runtime.py](../app/recovery_runtime.py) 도 hard gate timeout / recoverable heuristic 결과 / recovery success-failure를 같은 artifact에 남긴다.
- [dashboard.py](../app/dashboard.py) job detail API는 `runtime_recovery_trace`를 반환한다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_runtime_recovery_trace.py](../tests/test_runtime_recovery_trace.py)
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-B1 failure classification

- [failure_classification.py](../app/failure_classification.py) 를 추가해 runtime failure evidence를 normalized class로 분류하도록 만들었다.
- 현재 baseline class:
  - `provider_quota`
  - `provider_timeout`
  - `provider_auth`
  - `stale_heartbeat`
  - `git_conflict`
  - `test_failure`
  - `tool_failure`
  - `workflow_contract`
  - `unknown_runtime`
- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) event는 이제 `failure_class`를 함께 기록한다.
- [dashboard.py](../app/dashboard.py) jobs API와 job detail API는 `failure_classification` summary와 `runtime_recovery_trace.latest_failure_class`를 반환한다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_failure_classification.py](../tests/test_failure_classification.py)
  - [test_runtime_recovery_trace.py](../tests/test_runtime_recovery_trace.py)
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 5-B2 stage/provider mapping

- [failure_classification.py](../app/failure_classification.py) 가 이제 `failure_class`뿐 아니라 `provider_hint`, `stage_family`도 계산한다.
- runtime recovery trace event는 이제 아래를 같이 기록한다.
  - `failure_class`
  - `provider_hint`
  - `stage_family`
- [dashboard.py](../app/dashboard.py) job detail API는 아래 최신 요약을 반환한다.
  - `runtime_recovery_trace.latest_provider_hint`
  - `runtime_recovery_trace.latest_stage_family`
  - `failure_classification.provider_hint`
  - `failure_classification.stage_family`
- jobs API도 `failure_provider_hint`, `failure_stage_family`를 반환하고 검색 haystack에 포함한다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_failure_classification.py](../tests/test_failure_classification.py)
  - [test_runtime_recovery_trace.py](../tests/test_runtime_recovery_trace.py)
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 5-B3 dashboard visibility

- [app/templates/index.html](../app/templates/index.html) 작업 목록은 이제 실패 분류 힌트를 같이 보여준다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) 작업 상세는 아래를 직접 노출한다.
  - `실패 분류`
  - `실패 공급자`
  - `실패 단계군`
  - `Failure Classification` 보드
- [app/static/style.css](../app/static/style.css) 에 목록 힌트 스타일을 추가했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-C3 needs_human hardening

- [needs_human_policy.py](../app/needs_human_policy.py) 를 추가해 `needs_human` 상태를 구조화된 operator handoff summary로 정리했다.
- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) 는 이제 `decision="needs_human"` 또는 `recovery_status="needs_human"` event에 `needs_human_summary`를 같이 남긴다.
- [job_failure_runtime.py](../app/job_failure_runtime.py) 의 standard retry fast-fail path도 `needs_human` trace event를 남긴다.
- [recovery_runtime.py](../app/recovery_runtime.py) 의 hard gate policy fast-fail도 같은 handoff trace를 남긴다.
- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 trace/job 상태를 바탕으로 `needs_human_summary`를 계산한다.
- [dashboard.py](../app/dashboard.py) job detail API는 `needs_human_summary`를 반환한다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) 은 `사람 확인 필요` 보드에서 아래를 보여준다.
  - 제목
  - 요약
  - 복구 경로
  - 수동 재개 권장 여부
  - cooldown / 자동 재시도 예산
  - 권장 조치 목록
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_needs_human_policy.py](../tests/test_needs_human_policy.py)
  - [test_runtime_recovery_trace.py](../tests/test_runtime_recovery_trace.py)
  - [test_job_failure_runtime.py](../tests/test_job_failure_runtime.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-D1 provider failure counters

- [provider_failure_counter_runtime.py](../app/provider_failure_counter_runtime.py) 를 추가해 workspace 단위 `PROVIDER_FAILURE_COUNTERS.json` artifact를 만들었다.
- [workflow_resume.py](../app/workflow_resume.py) artifact path map에 `provider_failure_counters`를 추가했다.
- [job_failure_runtime.py](../app/job_failure_runtime.py) 의 standard retry loop는 provider-like 실패를 만나면 같은 workspace counter를 누적한다.
- [recovery_runtime.py](../app/recovery_runtime.py) 의 hard gate policy path도 provider-like 실패를 같은 counter에 누적한다.
- [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 workspace별 provider counter artifact를 읽어 admin metrics `runtime.provider_failure_counts`를 집계한다.
- [app/templates/index.html](../app/templates/index.html) 운영 지표에는 `공급자 실패 카운터` 카드가 추가됐다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_provider_failure_counter_runtime.py](../tests/test_provider_failure_counter_runtime.py)
  - [test_job_failure_runtime.py](../tests/test_job_failure_runtime.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 5-D2 cooldown window

- [provider_failure_counter_runtime.py](../app/provider_failure_counter_runtime.py) 는 이제 `evaluate_provider_cooldown()` 과 `format_provider_cooldown_reason()` 을 제공한다.
- provider cooldown threshold는 baseline 기준으로 아래처럼 동작한다.
  - `provider_timeout`: 최근 2회 반복 + cooldown `120s`
  - `tool_failure`: 최근 2회 반복 + cooldown `120s`
  - `provider_quota`: policy상 여전히 `needs_human` 우선이지만 cooldown seconds `900`도 같이 가진다.
  - `provider_auth`: policy상 여전히 `needs_human` 우선이지만 cooldown seconds `300`도 같이 가진다.
- [job_failure_runtime.py](../app/job_failure_runtime.py) 의 standard retry loop는 반복 provider failure를 만나면 `cooldown_wait`로 전이하고 `RUNTIME_RECOVERY_TRACE.json` 에 `decision="cooldown_wait"` event를 남긴다.
- [recovery_runtime.py](../app/recovery_runtime.py) 의 hard gate도 반복 provider timeout/tool failure를 만나면 같은 `cooldown_wait` trace를 남기고 즉시 중단한다.
- hard gate 내부 반복 실패와 상위 standard retry loop 사이의 중복 카운트를 줄이기 위해 provider failure record에 `occurrence_key` 를 추가했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_provider_failure_counter_runtime.py](../tests/test_provider_failure_counter_runtime.py)
  - [test_retry_policy.py](../tests/test_retry_policy.py)
  - [test_job_failure_runtime.py](../tests/test_job_failure_runtime.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)

### Phase 5-E1 dead-letter state

- [dead_letter_policy.py](../app/dead_letter_policy.py) 를 추가해 dead-letter 상태를 구조화된 operator summary로 정리했다.
- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) 는 이제 `decision="dead_letter"` 또는 `recovery_status="dead_letter"` event에 `dead_letter_summary`를 같이 남긴다.
- [job_failure_runtime.py](../app/job_failure_runtime.py) 는 특별한 recovery 상태가 없는 최종 실패를 `failed + recovery_status=dead_letter`로 표준화한다.
- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 trace/job 상태를 바탕으로 `dead_letter_summary`를 계산한다.
- [dashboard.py](../app/dashboard.py) job detail API는 `dead_letter_summary`를 반환한다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) 은 `Dead Letter 격리` 보드에서 아래를 보여준다.
  - 제목
  - 요약
  - 상위 복구 상태
  - 수동 재개 가능 여부
  - 새 작업 재시도 권장 여부
  - 권장 조치 목록
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_dead_letter_policy.py](../tests/test_dead_letter_policy.py)
  - [test_runtime_recovery_trace.py](../tests/test_runtime_recovery_trace.py)
  - [test_job_failure_runtime.py](../tests/test_job_failure_runtime.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-E2 retry from dead-letter action

- [dashboard.py](../app/dashboard.py) 에 `POST /api/jobs/{job_id}/dead-letter/retry`를 추가했다.
- 이 경로는 `failed + recovery_status=dead_letter` 상태 작업만 다시 큐에 넣는다.
- 재큐잉 시 아래 상태 전이를 명시적으로 남긴다.
  - `status=queued`
  - `stage=queued`
  - `attempt=0`
  - `recovery_status=dead_letter_requeued`
  - `recovery_reason=운영자 재큐잉 사유`
- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) 에 `source="dashboard_dead_letter_retry"`, `decision="retry_from_dead_letter"` trace를 남긴다.
- trace details에는 아래가 포함된다.
  - `previous_recovery_status`
  - `previous_reason`
  - `operator_note`
  - `retry_from_scratch`
- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 이제 현재 job 상태가 `dead_letter`일 때만 `dead_letter_summary`를 다시 보여준다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) 은 `Dead Letter 격리` 보드에 `Dead Letter 다시 큐에 넣기` 버튼을 붙였다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_manual_workflow_retry.py](../tests/test_manual_workflow_retry.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-E3 operator note + approval trail

- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 에 `build_job_dead_letter_action_trail()` 를 추가했다.
- 이 helper는 `runtime_recovery_trace`에서 아래 이벤트만 읽어 최근 5개 조치 이력을 만든다.
  - `decision=dead_letter`
  - `decision=retry_from_dead_letter`
  - `recovery_status=dead_letter`
  - `recovery_status=dead_letter_requeued`
- [dashboard.py](../app/dashboard.py) job detail API는 이제 `dead_letter_action_trail`을 같이 반환한다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) 은 `Dead Letter 격리` 보드에서 아래를 보여준다.
  - 최근 조치 시각
  - decision
  - reason
  - 운영자 메모
  - 이전 사유
- dead-letter 재큐잉 버튼은 이제 note textarea를 같이 보내고, 그 note가 trace details에 `operator_note`로 남는다.
- dead-letter 상태가 풀린 뒤에도 최근 조치 이력은 계속 보인다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_manual_workflow_retry.py](../tests/test_manual_workflow_retry.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-D3 provider quarantine baseline

- [provider_failure_counter_runtime.py](../app/provider_failure_counter_runtime.py) 는 이제 `evaluate_provider_quarantine()` 과 `format_provider_quarantine_reason()` 을 제공한다.
- quarantine threshold는 baseline 기준으로 아래처럼 동작한다.
  - `provider_timeout`: 최근 4회 반복 시 `provider_quarantined`
  - `tool_failure`: 최근 4회 반복 시 `provider_quarantined`
- [job_failure_runtime.py](../app/job_failure_runtime.py) 의 standard retry loop는 repeated provider burst를 만나면 `provider_quarantined`로 전이하고 trace에 `decision="provider_quarantined"`를 남긴다.
- [recovery_runtime.py](../app/recovery_runtime.py) 의 hard gate도 같은 기준으로 `provider_quarantined` trace를 남기고 즉시 중단한다.
- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) 는 `provider_quarantined` event를 `needs_human_summary`로도 구조화해서 기존 handoff 보드에 연결한다.
- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 `recovery_status=provider_quarantined`도 `needs_human_summary` 대상에 포함한다.

### Phase 5-D4 provider circuit-breaker baseline

- [provider_failure_counter_runtime.py](../app/provider_failure_counter_runtime.py) 는 이제 `evaluate_provider_circuit_breaker()` 와 `evaluate_workspace_provider_circuit_breaker()` 를 제공한다.
- circuit-breaker threshold는 baseline 기준으로 아래처럼 동작한다.
  - `provider_timeout`: 최근 6회 반복 시 `provider_circuit_open`
  - `tool_failure`: 최근 6회 반복 시 `provider_circuit_open`
- [job_failure_runtime.py](../app/job_failure_runtime.py) 의 standard retry loop는 extended provider burst를 만나면 `provider_circuit_open`으로 전이하고 trace에 `decision="provider_circuit_open"`를 남긴다.
- [recovery_runtime.py](../app/recovery_runtime.py) 의 hard gate도 같은 기준으로 `provider_circuit_open` trace를 남기고 즉시 중단한다.
- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) 는 `provider_circuit_open` event를 `needs_human_summary`로 구조화하고 `recovery_path="provider_circuit_breaker"`를 남긴다.
- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 `recovery_status=provider_circuit_open`도 `needs_human_summary` 대상으로 읽는다.
- [orchestrator.py](../app/orchestrator.py) 의 planner/reviewer repository-aware template selector는 workspace provider가 `provider_circuit_open` 상태여도 fallback 템플릿을 우선 선택한다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_provider_failure_counter_runtime.py](../tests/test_provider_failure_counter_runtime.py)
  - [test_job_failure_runtime.py](../tests/test_job_failure_runtime.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)
  - [test_runtime_recovery_trace.py](../tests/test_runtime_recovery_trace.py)
  - [test_ai_role_routing.py](../tests/test_ai_role_routing.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-G1 dead-letter list / recovery history summary

- [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 admin metrics payload에 아래를 같이 실어준다.
  - `runtime.dead_letter_jobs`
  - `runtime.recovery_history.event_counts`
  - `runtime.recovery_history.recent_events`
- `dead_letter_jobs` 는 최근 `recovery_status=dead_letter` 작업을 job 목록 기준으로 정렬해 반환한다.
- `recovery_history` 는 workspace별 `RUNTIME_RECOVERY_TRACE.json` 을 모아 최근 recovery decision trail을 합쳐서 반환한다.
- [app/templates/index.html](../app/templates/index.html) 운영 지표에는 아래 카드가 추가됐다.
  - `Dead Letter 작업`
  - `최근 복구 이력`
- 운영자는 이제 admin 화면에서 아래를 파일 로그 없이 바로 읽을 수 있다.
  - 어떤 작업이 dead-letter 상태인지
  - 최근 recovery decision이 `dead_letter / provider_circuit_open / requeue / needs_human` 중 무엇이었는지
  - 어떤 provider/stage family에서 recovery가 일어났는지
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_provider_failure_counter_runtime.py](../tests/test_provider_failure_counter_runtime.py)
  - [test_job_failure_runtime.py](../tests/test_job_failure_runtime.py)
  - [test_recovery_runtime.py](../tests/test_recovery_runtime.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-G2 provider/startup audit history surface

- [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 admin metrics payload에 아래를 같이 실어준다.
  - `runtime.provider_outage_history.event_counts`
  - `runtime.provider_outage_history.provider_counts`
  - `runtime.provider_outage_history.recent_events`
  - `runtime.startup_sweep_history`
- `provider_outage_history` 는 workspace별 `RUNTIME_RECOVERY_TRACE.json` 에서 provider 계열 failure class / decision을 모아 최근 outage trail을 반환한다.
- `startup_sweep_history` 는 `worker_startup_sweep_trace.json` 최근 event를 정렬해서 mismatch before/after 요약과 함께 반환한다.
- [app/templates/index.html](../app/templates/index.html) 운영 지표에는 아래 카드가 추가됐다.
  - `공급자 장애 이력`
  - `재시작 감사 이력`
- 운영자는 이제 admin 화면에서 아래를 파일 로그 없이 바로 읽을 수 있다.
  - 어떤 provider가 최근 `provider_circuit_open / provider_quarantined / cooldown_wait`로 전이됐는지
  - 어떤 job/stage family가 그 outage와 연결됐는지
  - worker 재시작 때 mismatch가 얼마나 감지됐고 얼마나 남았는지
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 5-G3 dead-letter / recovery action drilldown

- [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 dead-letter / recovery drilldown용 facet 요약도 같이 실어준다.
  - `runtime.dead_letter_summary.app_counts`
  - `runtime.dead_letter_summary.failure_class_counts`
  - `runtime.dead_letter_summary.provider_counts`
  - `runtime.recovery_history.provider_counts`
  - `runtime.recovery_history.stage_family_counts`
- [app/templates/index.html](../app/templates/index.html) 운영 지표에서는 아래 drilldown filter를 바로 쓸 수 있다.
  - `Dead Letter 작업`: 앱 / 실패 분류 / 공급자
  - `최근 복구 이력`: 결정 / 공급자 / 단계군
- drilldown은 client-side filter라서 mutation 없이 현재 payload 범위 안에서 바로 상태별 탐색이 가능하다.
- 운영자는 이제 아래를 한 화면에서 바로 줄여 볼 수 있다.
  - 특정 앱의 dead-letter 작업만 보기
  - 특정 공급자/실패 분류 dead-letter만 보기
  - 특정 recovery decision 또는 stage family만 보기
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Richer operator ops surface baseline

- [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 admin metrics payload에 아래를 추가로 실어준다.
  - `runtime.recovery_action_groups.action_counts`
  - `runtime.recovery_action_groups.source_counts`
  - `runtime.recovery_action_groups.recent_actions`
  - `runtime.operator_action_trail.source_counts`
  - `runtime.operator_action_trail.decision_counts`
  - `runtime.operator_action_trail.recent_events`
- [app/templates/index.html](../app/templates/index.html) 운영 지표에는 아래 카드가 추가됐다.
  - `복구 액션 그룹`
  - `운영자 조치 이력`
- 운영자는 이제 admin 화면에서 아래를 한 번에 볼 수 있다.
  - 최근 recovery trace가 dead-letter / requeue / human_handoff / provider_outage 중 어디에 몰리는지
  - dashboard에서 발생한 dead-letter retry 같은 operator-triggered action과 note
  - provider/stage/source 기준의 조치 흐름
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### Phase 5-D3 alternate route fallback

- [orchestrator.py](../app/orchestrator.py) 는 이제 `_template_for_route_in_repository()` 를 통해 workspace provider counter를 읽고, `planner/reviewer` 경로에서 quarantine active 시 fallback 템플릿을 우선 선택한다.
- [planner_runtime.py](../app/planner_runtime.py) 와 [review_fix_runtime.py](../app/review_fix_runtime.py) 는 repository-aware template selector를 받아 `planner/reviewer` 실행에 바로 적용한다.
- [provider_failure_counter_runtime.py](../app/provider_failure_counter_runtime.py) 는 `evaluate_workspace_provider_quarantine()` 를 제공해 workspace artifact 기준 격리 상태를 읽는다.
- [config/ai_commands.json](../config/ai_commands.json) 과 [config/ai_commands.example.json](../config/ai_commands.example.json) 의 `planner_fallback`, `planner__gemini_fallback`, `reviewer_fallback`, `reviewer__gemini_fallback` 는 이제 Codex 명령으로 연결된다.
- 현재 baseline은 아래처럼 동작한다.
  - Gemini provider burst가 workspace 기준으로 quarantine 되면 `planner`는 `planner_fallback` 계열을 우선 선택한다.
  - Gemini provider burst가 workspace 기준으로 quarantine 되면 `reviewer`는 `reviewer_fallback` 계열을 우선 선택한다.
  - fallback 템플릿이 없으면 기존 primary template을 그대로 사용한다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_provider_failure_counter_runtime.py](../tests/test_provider_failure_counter_runtime.py)
  - [test_ai_role_routing.py](../tests/test_ai_role_routing.py)
  - [test_planner_runtime.py](../tests/test_planner_runtime.py)
  - [test_review_fix_runtime.py](../tests/test_review_fix_runtime.py)

### Phase 5-F1 startup sweep trace

- [worker_startup_sweep_runtime.py](../app/worker_startup_sweep_runtime.py) 를 추가해서 worker 시작 시 정리 결과를 `data/worker_startup_sweep_trace.json` 에 누적 기록한다.
- [worker_main.py](../app/worker_main.py) 는 이제 `_run_startup_sweep()` 를 통해 아래를 한 번에 실행하고 trace를 남긴다.
  - orphan running node run interruption
  - stale running job auto-recovery
  - orphan queued job requeue 시도
- 이 슬라이스는 `trace 추가`가 목적이라 기존 orphan queued recovery 규칙은 그대로 유지했다.
  - queue가 이미 비어 있지 않으면 orphan queued recovery는 건너뛴다.
- startup sweep trace event는 아래를 남긴다.
  - `orphan_running_node_runs_interrupted`
  - `stale_running_jobs_recovered`
  - `orphan_queued_jobs_recovered`
  - `queue_size_before`
  - `queue_size_after`
  - worker 설정 요약
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)
  - [test_worker_startup_sweep_runtime.py](../tests/test_worker_startup_sweep_runtime.py)

### Phase 5-F2 restart-safe requeue reason

- [requeue_reason_runtime.py](../app/requeue_reason_runtime.py) 를 추가해 stale auto-recovery, dead-letter 재큐잉, 수동 workflow retry를 하나의 `requeue_reason_summary` shape로 구조화했다.
- [runtime_recovery_trace.py](../app/runtime_recovery_trace.py) 는 이제 `decision/recovery_status`가 재큐잉 계열이면 `requeue_reason_summary`를 자동으로 남긴다.
- [dashboard.py](../app/dashboard.py) 의 수동 workflow retry API는 이제 `dashboard_manual_retry` source trace를 남긴다.
- [dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 trace/job 상태를 바탕으로 `requeue_reason_summary`를 계산한다.
- [dashboard.py](../app/dashboard.py) job detail API는 `requeue_reason_summary`를 반환한다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) 은 `재큐잉 사유` 보드에서 아래를 보여준다.
  - 제목
  - 요약
  - 트리거/출처/결정
  - 복구 상태
  - 처음부터 재시도 여부
  - 시작 노드
  - 이전 복구 상태/이전 사유
  - 운영자 메모
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_requeue_reason_runtime.py](../tests/test_requeue_reason_runtime.py)
  - [test_runtime_recovery_trace.py](../tests/test_runtime_recovery_trace.py)
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)
  - [test_manual_workflow_retry.py](../tests/test_manual_workflow_retry.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### Phase 5-F3 running node/job mismatch audit

- [worker_startup_sweep_runtime.py](../app/worker_startup_sweep_runtime.py) 에 `audit_running_node_job_mismatches()` 를 추가했다.
- mismatch baseline은 아래를 본다.
  - `non_running_job_has_running_node_runs`
  - `running_job_missing_current_running_node`
  - `running_job_has_stale_running_node_attempt`
  - `running_job_has_multiple_current_running_nodes`
- [worker_main.py](../app/worker_main.py) 의 `_run_startup_sweep()` 는 이제 cleanup/recovery 전후 audit를 수행하고 `worker_startup_sweep_trace.json` 에 아래를 남긴다.
  - `running_node_job_mismatches_detected`
  - `running_node_job_mismatches_remaining`
  - `details.mismatch_audit_before`
  - `details.mismatch_audit_after`
- worker 시작 로그도 mismatch detected/remaining를 바로 출력한다.
- [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 최신 startup sweep event를 읽어 `runtime.startup_sweep` 요약을 반환한다.
- [app/templates/index.html](../app/templates/index.html) 운영 지표에는 `Worker 재시작 감사` 카드가 추가됐다.
  - 최근 감사 시각
  - 감지된 mismatch 수
  - 남은 mismatch 수
  - mismatch type 분포
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_worker_startup_sweep_runtime.py](../tests/test_worker_startup_sweep_runtime.py)
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

### 기존 리팩터링 누적 상태

- `dashboard.py` 쪽 분리 완료:
  - [agent_cli_runtime.py](../app/agent_cli_runtime.py)
  - [assistant_runtime.py](../app/assistant_runtime.py)
  - [agent_config_runtime.py](../app/agent_config_runtime.py)
  - [dashboard_job_runtime.py](../app/dashboard_job_runtime.py)
  - [dashboard_integration_registry_runtime.py](../app/dashboard_integration_registry_runtime.py)
  - [dashboard_runtime_input_runtime.py](../app/dashboard_runtime_input_runtime.py)
  - [dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py)
- `orchestrator.py` 쪽 분리 완료:
  - [summary_runtime.py](../app/summary_runtime.py)
  - [content_stage_runtime.py](../app/content_stage_runtime.py)
  - [review_fix_runtime.py](../app/review_fix_runtime.py)
  - [planner_runtime.py](../app/planner_runtime.py)
  - [implement_runtime.py](../app/implement_runtime.py)
  - [workflow_node_runtime.py](../app/workflow_node_runtime.py)
  - [workflow_pipeline_runtime.py](../app/workflow_pipeline_runtime.py)
  - [provider_runtime.py](../app/provider_runtime.py)
  - [preview_runtime.py](../app/preview_runtime.py)
  - [app_type_runtime.py](../app/app_type_runtime.py)
  - [product_definition_runtime.py](../app/product_definition_runtime.py)
  - [improvement_runtime.py](../app/improvement_runtime.py)
  - [memory_retrieval_runtime.py](../app/memory_retrieval_runtime.py)
  - [memory_quality_runtime.py](../app/memory_quality_runtime.py)
  - [structured_memory_runtime.py](../app/structured_memory_runtime.py)
  - [design_governance_runtime.py](../app/design_governance_runtime.py)
  - [integration_recommendation_runtime.py](../app/integration_recommendation_runtime.py)
  - [ux_review_runtime.py](../app/ux_review_runtime.py)
  - [workspace_repository_runtime.py](../app/workspace_repository_runtime.py)
  - [workflow_resolution_runtime.py](../app/workflow_resolution_runtime.py)
  - [docs_snapshot_runtime.py](../app/docs_snapshot_runtime.py)

## 2. 현재 상태 판단

- 문서와 소스의 싱크는 현재 기준으로 다시 맞췄다.
- 하지만 시스템 판정은 여전히 `강한 기반을 가진 고급 프로토타입`이다.
- 지금 우선순위는 여전히 `기능 확장`보다 `리팩터링 + 운영 신뢰성`이다.
- dashboard 쪽 1차 전환 기준은 넘겼다.
  - [app/dashboard.py](../app/dashboard.py): `3804` lines
  - [app/orchestrator.py](../app/orchestrator.py): `3257` lines
- `Phase 5-A1 Runtime Recovery Trace`는 구현됐다.
- `Phase 5-B1 Failure Classification`도 구현됐다.
- `Phase 5-B2 stage/provider mapping`도 구현됐다.
- `Phase 5-B3 dashboard visibility`도 구현됐다.
- `job_failure_runtime` 분리도 구현됐다.
- `Phase 5-C1 retry policy table`도 구현됐고, standard retry loop 기준 baseline enforcement가 들어갔다.
- `Phase 5-C2 retry budget enforcement`도 baseline 기준으로 구현됐고, hard gate와 worker stale recovery가 같은 selector를 보기 시작했다.
- `Phase 5-C3 needs_human hardening`도 baseline 기준으로 구현됐다.
- `Phase 5-D2 cooldown window`도 baseline 기준으로 구현됐다.
- `Phase 5-D3 provider quarantine baseline`도 구현됐다.
- `Phase 5-D3 alternate route fallback`도 baseline 기준으로 구현됐다.
- `Phase 5-E1 dead-letter state`도 baseline 기준으로 구현됐다.
- `Phase 5-E2 retry from dead-letter action`도 baseline 기준으로 구현됐다.
- `Phase 5-E3 operator note + approval trail`도 baseline 기준으로 구현됐다.
- `Phase 5-F1 startup sweep trace`도 baseline 기준으로 구현됐다.
- `Phase 5-F2 restart-safe requeue reason`도 baseline 기준으로 구현됐다.
- `Phase 5-F3 running node/job mismatch audit`도 baseline 기준으로 구현됐다.
- `Phase 5-D4 provider circuit-breaker baseline`도 baseline 기준으로 구현됐다.
- `Phase 5-G1 dead-letter list / recovery history summary`도 baseline 기준으로 구현됐다.
- `Phase 5-G2 provider/startup audit history surface`도 baseline 기준으로 구현됐다.
- `Phase 5-G3 dead-letter / recovery action drilldown`도 baseline 기준으로 구현됐다.
- `richer operator ops surface` baseline도 기준선까지 구현됐다.
- 현재 전략 우선순위는 `Phase 6 operator control plane / integration registry`로 전환하는 게 맞다.
- 잔여 runtime split은 계속 필요하지만, 이제는 Phase 6 첫 슬라이스를 막지 않는 범위에서 병행할 기술 부채에 가깝다.
- `Phase 6-E1 failed job operator approval boundary`도 구현됐다.
  - failed / needs_human / dead_letter / provider_quarantined / provider_circuit_open job detail은 이제 `integration_operator_boundary` payload를 통해 통합 승인/입력 부족 때문에 막힌 후보와 권장 조치를 직접 보여준다.
- `Phase 6-F1 integration usage trail`도 구현됐다.
  - planner/coder/reviewer prompt 주입 시 승인된 통합과 blocked env를 `_docs/INTEGRATION_USAGE_TRAIL.json`로 append-only 기록한다.
- `Phase 6-F2 missing-input / auth / quota facet`도 구현됐다.
  - job detail은 이제 `integration_health_facets`를 통해 missing input, provider auth, provider quota blocker를 한 화면에서 보여준다.
- `Phase 6-F3 integration health summary`도 구현됐다.
  - admin metrics는 이제 승인 상태, 준비 상태, 최근 사용 통합, 차단 경계, 자주 막히는 env, 최근 막힌 작업을 `integration_health_summary`로 함께 보여준다.
- `remaining runtime split`의 다음 조각으로 `product_review_runtime` 분리도 구현됐다.
  - `PRODUCT_REVIEW.json`, `REPO_MATURITY.json`, `QUALITY_TREND.json`, `IMPROVEMENT_BACKLOG.json` 생성과 evidence/trend 계산이 [app/product_review_runtime.py](../app/product_review_runtime.py) 로 빠졌고, [app/orchestrator.py](../app/orchestrator.py)는 wrapper만 남겼다.
- `remaining runtime split`의 다음 조각으로 `artifact_io_runtime` 분리도 구현됐다.
  - JSON artifact write/read, JSONL upsert, JSON history upsert, REVIEW TODO 추출, stable issue id 생성이 [app/artifact_io_runtime.py](../app/artifact_io_runtime.py) 로 빠졌고, [app/orchestrator.py](../app/orchestrator.py)는 thin wrapper만 유지한다.
- `remaining runtime split`의 다음 조각으로 `design_governance_runtime` 분리도 구현됐다.
  - design-system decision lock, `_docs/DECISIONS.json`, `STAGE_CONTRACTS.*`, `PIPELINE_ANALYSIS.*` helper가 [app/design_governance_runtime.py](../app/design_governance_runtime.py) 로 빠졌고, [app/orchestrator.py](../app/orchestrator.py)는 workflow node contract를 깨지 않게 wrapper만 유지한다.
- `remaining runtime split`의 다음 조각으로 `product-review operating principle alignment`도 [app/product_review_runtime.py](../app/product_review_runtime.py) 로 흡수됐다.
  - [app/orchestrator.py](../app/orchestrator.py) 는 `_build_operating_principle_alignment()` wrapper만 유지하고, 본문은 product-review runtime 정적 helper를 재사용한다.
- `remaining runtime split`의 다음 조각으로 `job_log_runtime` 분리도 구현됐다.
  - [app/job_log_runtime.py](../app/job_log_runtime.py) 가 actor log writer, debug/user channel routing, user-log emission filter, job heartbeat touch를 맡는다.
- `remaining runtime split`의 다음 조각으로 `job_control_runtime` 분리도 구현됐다.
  - [app/job_control_runtime.py](../app/job_control_runtime.py) 가 stop signal path/check/clear, agent profile normalize, require_job lookup을 맡는다.
- `remaining runtime split`의 다음 조각으로 `job_mode_runtime` 분리도 구현됐다.
  - [app/job_mode_runtime.py](../app/job_mode_runtime.py) 가 escalation toggle, recovery mode toggle, long/ultra/ultra10 track 판별을 맡는다.
  - [app/orchestrator.py](../app/orchestrator.py) 는 `_actor_log_writer()`, `_append_actor_log()`, `_touch_job_heartbeat()`, `_channel_log_path()`, `_should_emit_user_log()`, `_append_log()` wrapper만 유지한다.
- `remaining runtime split`의 다음 조각으로 `issue_spec_runtime` 분리도 구현됐다.
  - [app/issue_spec_runtime.py](../app/issue_spec_runtime.py) 가 `gh issue view` 기반 canonical issue load와 `SPEC.md / SPEC.json / SPEC_QUALITY.json` 생성, quality rewrite loop, stage contract / pipeline analysis doc write, issue title/url metadata sync를 맡는다.
  - [app/orchestrator.py](../app/orchestrator.py) 는 `_stage_read_issue()` 와 `_stage_write_spec()` wrapper만 유지한다.
- `remaining runtime split`의 다음 조각으로 `template_artifact_runtime` 분리도 구현됐다.
  - [app/template_artifact_runtime.py](../app/template_artifact_runtime.py) 가 공통 template variable 생성과 `DESIGN_TOKENS / TOKEN_HANDOFF / PUBLISH_CHECKLIST / PUBLISH_HANDOFF / COPYWRITING_PLAN / COPY_DECK / DOCUMENTATION_PLAN` fallback helper를 맡는다.
  - [app/orchestrator.py](../app/orchestrator.py) 는 `_build_template_variables()`, `_ensure_design_artifacts()`, `_ensure_publisher_artifacts()`, `_ensure_copywriter_artifacts()`, `_ensure_documentation_artifacts()` wrapper만 유지한다.
- `remaining runtime split`의 다음 조각으로 `tool_support_runtime` 분리도 구현됐다.
  - [app/tool_support_runtime.py](../app/tool_support_runtime.py) 가 planner/tool runtime이 쓰는 local evidence fallback, scoped memory search, vector-backed memory search를 맡는다.
  - [app/orchestrator.py](../app/orchestrator.py) 는 `_build_local_evidence_fallback()`, `_search_memory_entries_for_tool()`, `_search_vector_memory_entries_for_tool()` wrapper만 유지한다.
- `remaining runtime split`의 다음 조각으로 `summary_runtime`이 `commit stage` 본문도 흡수했다.
  - [app/summary_runtime.py](../app/summary_runtime.py) 가 이제 `git status -> git add -> AI commit summary -> git commit` 흐름을 맡는다.
  - [app/orchestrator.py](../app/orchestrator.py) 는 `_stage_commit()` wrapper만 유지한다.
- `remaining runtime split`의 다음 조각으로 `fixed_pipeline_runtime` 분리도 구현됐다.
  - [app/fixed_pipeline_runtime.py](../app/fixed_pipeline_runtime.py) 가 legacy fixed pipeline 본문 전체를 맡는다.
  - [app/orchestrator.py](../app/orchestrator.py) 는 `_run_fixed_pipeline()` wrapper만 유지한다.

## 3. 다음 우선순위

1. `remaining runtime split`
   - orchestrator 잔여 helper를 계속 줄이는 마감 단계

2. `enterprise 운영 계층 보강`
   - usage trail, approval 경계, provider containment를 운영 surface로 더 조밀하게 엮는 단계

3. `dashboard write action/service 잔여 축소`
   - job action baseline은 분리됐고, 남은 admin mutation / operator write 경계를 더 잘게 분리하는 단계

## 4. 주의할 점

- `LICENSE`는 아직 미정이다. 기술 패치로 임의 추가하지 않는다.
- 실제 운영 시크릿 로테이션과 Git 히스토리 정리는 아직 수행되지 않았다.
- `config/ai_commands.json`은 로컬 런타임 파일이다. 예시 파일과 혼동하지 않는다.
- 레거시 `Claude/Copilot` 이름은 일부 호환 alias로 남아 있다. 실제 기본 실행 경로는 `Gemini + Codex` 기준이다.
- feature 수만 늘리는 건 지금 해결책이 아니다. 구조 리스크와 운영 신뢰성부터 낮춰야 한다.
- 모바일 앱 검증 artifact는 baseline까지만 자동화되어 있고, safe-area/keyboard/offline의 실제 판정은 아직 수동 확인 항목으로 남아 있다.

## 5. 검증 결과

- preview/provider runtime 타깃 회귀: `5 passed`
- ux review runtime 타깃 회귀: `4 passed`
- Phase 6-D2 code pattern/snippet 타깃 회귀: `13 passed`
- Phase 6-D3 verification checklist 타깃 회귀: `14 passed`
- Phase 6-E1 타깃 회귀: `24 passed, 1 warning`
- Phase 6-F3 integration health summary 타깃 회귀: `20 passed`
- product review runtime 타깃 회귀: `8 passed, 43 deselected`
- artifact io runtime 타깃 회귀: `11 passed, 43 deselected`
- design governance runtime 타깃 회귀: `4 passed, 50 deselected`
- product review alignment 타깃 회귀: `10 passed, 42 deselected`
- 최신 전체 회귀: `374 passed, 10 warnings`
- 모바일 앱 개발 모드 규칙 반영 타깃 회귀: `2 passed`
- dashboard roles runtime 타깃 회귀: `21 passed`
- workspace repository runtime 타깃 회귀: `5 passed`
- app type runtime 타깃 회귀: `3 passed, 3 deselected`
- product definition runtime 타깃 회귀: `7 passed, 45 deselected`
- improvement runtime 타깃 회귀: `12 passed, 36 deselected`
- memory retrieval runtime 타깃 회귀: `11 passed, 37 deselected`
- structured memory runtime 타깃 회귀: `4 passed, 44 deselected`
- integration registry / recommendation 타깃 회귀: `10 passed, 21 deselected`
- memory quality runtime 타깃 회귀: `3 passed, 45 deselected`
- workspace_app mobile mode 타깃 회귀: `2 passed`
- mobile app runner surface 타깃 회귀: `20 passed`
- mobile quality artifact 타깃 회귀: `8 passed`
- 로그/로그인 오류 노이즈 완화 타깃 회귀: `13 passed`
- admin metrics dead-letter/recovery history 타깃 회귀: `18 passed`
- admin metrics provider/startup history 타깃 회귀: `18 passed`
- admin dead-letter/recovery drilldown 타깃 회귀: `18 passed`
- admin recovery action groups/operator trail 타깃 회귀: `18 passed`
- provider circuit-breaker 타깃 회귀: `57 passed, 1 warning`
- running node/job mismatch audit 타깃 회귀: `25 passed`
- restart-safe requeue reason 타깃 회귀: `32 passed, 1 warning`
- startup sweep trace 타깃 회귀: `6 passed`
- alternate route fallback 타깃 회귀: `18 passed`
- dead-letter retry / note trail 타깃 회귀: `19 passed, 1 warning`
- provider quarantine 타깃 회귀: `38 passed, 1 warning`
- retry policy + hard gate/worker enforcement 타깃 회귀: `68 passed`
- job failure runtime 타깃 회귀: `50 passed`
- failure classification 타깃 회귀: `44 passed`
- dashboard visibility 타깃 회귀: `28 passed`
- recovery trace 타깃 회귀: `21 passed`
- dashboard admin metrics 타깃 회귀: `18 passed`
- dashboard runtime input 타깃 회귀: `20 passed`
- dashboard job runtime 타깃 회귀: `28 passed`
- docs snapshot/runtime 타깃 회귀: `54 passed`
- workflow resolution/runtime 타깃 회귀: `63 passed`
- provider runtime + summary fallback 타깃 회귀: `53 passed`
- workflow pipeline/runtime 타깃 회귀: `66 passed`
- workflow node runtime 타깃 회귀: `64 passed`
- assistant/provider 리팩터 타깃 회귀: `30 passed`
- agent config / template safety 리팩터 타깃 회귀: `19 passed`
- summary runtime 리팩터 타깃 회귀: `49 passed`
- content stage runtime 리팩터 타깃 회귀: `49 passed`
- review/fix runtime 리팩터 타깃 회귀: `49 passed`
- planner runtime 리팩터 타깃 회귀: `49 passed`
- implement runtime 리팩터 타깃 회귀: `50 passed`

## 6. 이번 턴 업데이트

### Phase 6-B3 env bridge policy hardening

- integration-linked env는 이제 linked integration이 `ready`일 때만 runtime env bridge에 들어간다.
- approval pending / rejected / input missing 상태의 env는 주입되지 않고 `blocked_inputs`, `blocked_env_vars`로 남는다.
- job detail의 operator inputs surface에서 정책상 차단된 입력과 차단 사유를 바로 볼 수 있다.
- 변경 파일:
  - [app/runtime_inputs.py](../app/runtime_inputs.py)
  - [app/orchestrator.py](../app/orchestrator.py)
  - [app/dashboard_job_runtime.py](../app/dashboard_job_runtime.py)
  - [app/templates/job_detail.html](../app/templates/job_detail.html)

### Phase 6-D2 code pattern/snippet hint

- 승인된 통합만 `_docs/INTEGRATION_CODE_PATTERNS.md` 로 요약해 planner/coder/reviewer prompt에 주입한다.
- 구현 가이드에서 아래를 prompt-safe 형태로 추출한다.
  - 코드 패턴 bullet 힌트
  - redacted snippet hint
  - verification hint
- secret 값처럼 보이는 assignment는 snippet에서 `<REDACTED>` 로 마스킹한다.
- 변경 파일:
  - [app/integration_guide_runtime.py](../app/integration_guide_runtime.py)
  - [app/prompt_builder.py](../app/prompt_builder.py)
  - [app/planner_runtime.py](../app/planner_runtime.py)
  - [app/implement_runtime.py](../app/implement_runtime.py)
  - [app/review_fix_runtime.py](../app/review_fix_runtime.py)
  - [app/orchestrator.py](../app/orchestrator.py)
  - [app/workflow_resume.py](../app/workflow_resume.py)

### Phase 6-D3 verification checklist injection

- 승인된 통합만 `_docs/INTEGRATION_VERIFICATION_CHECKLIST.md` 로 요약해 planner/coder/reviewer prompt에 주입한다.
- verification notes에서 아래를 prompt-safe 형태로 추출한다.
  - checklist bullet
  - verification summary
- checklist는 구현 self-check와 reviewer 검증 기준으로 같이 쓰인다.
- secret 값은 포함하지 않고 env var 이름과 검증 항목만 남긴다.
- 변경 파일:
  - [app/integration_guide_runtime.py](../app/integration_guide_runtime.py)
  - [app/prompt_builder.py](../app/prompt_builder.py)
  - [app/planner_runtime.py](../app/planner_runtime.py)
  - [app/implement_runtime.py](../app/implement_runtime.py)
  - [app/review_fix_runtime.py](../app/review_fix_runtime.py)
  - [app/orchestrator.py](../app/orchestrator.py)
  - [app/workflow_resume.py](../app/workflow_resume.py)

### Phase 6-E1 failed job operator approval boundary

- failed / needs_human / dead_letter / provider_quarantined / provider_circuit_open job detail은 이제 `integration_operator_boundary` payload를 같이 반환한다.
- 이 summary는 `_docs/INTEGRATION_RECOMMENDATIONS.json` 과 blocked/pending runtime input을 함께 읽어 아래 경계를 정규화한다.
  - `approval_and_input_required`
  - `approval_required`
  - `input_required`
- payload에는 아래가 포함된다.
  - `boundary_status`
  - `summary`
  - `recommended_actions`
  - `candidate_count`
  - `blocked_input_count`
  - `pending_input_count`
  - integration candidate별 `approval_status`, `input_readiness_status`, `blocked_inputs`
- 이번 슬라이스로 failure 운영과 integration/operator control이 job detail 한 화면에서 직접 연결됐다.
- 변경 파일:
  - [app/dashboard_job_runtime.py](../app/dashboard_job_runtime.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/job_detail.html](../app/templates/job_detail.html)
  - [tests/test_dashboard_job_runtime.py](../tests/test_dashboard_job_runtime.py)
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)

### Remaining runtime split: job_log_runtime

- [app/job_log_runtime.py](../app/job_log_runtime.py) 를 추가했다.
- 이 런타임은 아래 책임을 맡는다.
  - actor log writer 생성
  - debug/user channel log path 계산
  - user-facing log emission filter
  - timestamped log append
  - lightweight job heartbeat touch
- [app/orchestrator.py](../app/orchestrator.py) 는 이제 아래 wrapper만 유지한다.
  - `_actor_log_writer()`
  - `_infer_actor_from_command()`
  - `_append_actor_log()`
  - `_touch_job_heartbeat()`
  - `_channel_log_path()`
  - `_should_emit_user_log()`
  - `_append_log()`
- 이번 슬라이스로 오케스트레이터는 `3297` lines까지 내려왔다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_job_log_runtime.py](../tests/test_job_log_runtime.py)

### Remaining runtime split: job_control_runtime

- [app/job_control_runtime.py](../app/job_control_runtime.py) 를 추가했다.
- 이 런타임은 아래 책임을 맡는다.
  - stop signal path 계산
  - stop requested check
  - stop signal clear
  - active agent profile normalize
  - required job lookup
- [app/orchestrator.py](../app/orchestrator.py) 는 이제 아래 wrapper만 유지한다.
  - `_stop_signal_path()`
  - `_is_stop_requested()`
  - `_clear_stop_requested()`
  - `_set_agent_profile()`
  - `_require_job()`
- 이번 슬라이스로 오케스트레이터는 `3297` lines까지 내려왔다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_job_control_runtime.py](../tests/test_job_control_runtime.py)

### Remaining runtime split: job_mode_runtime

- [app/job_mode_runtime.py](../app/job_mode_runtime.py) 를 추가했다.
- 이 런타임은 아래 책임을 맡는다.
  - escalation toggle read
  - recovery mode toggle read
  - `long / ultra / ultra10` track 판별
- [app/orchestrator.py](../app/orchestrator.py) 는 이제 아래 wrapper만 유지한다.
  - `_is_escalation_enabled()`
  - `_is_recovery_mode_enabled()`
  - `_is_long_track()`
  - `_is_ultra_track()`
  - `_is_ultra10_track()`
- 이번 슬라이스로 오케스트레이터는 `3257` lines까지 내려왔다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_job_mode_runtime.py](../tests/test_job_mode_runtime.py)

### Self-Growing Bridge Effectiveness Baseline + Admin Summary

- [app/self_growing_effectiveness_runtime.py](../app/self_growing_effectiveness_runtime.py) 를 추가했다.
- follow-up job의 product review 종료 후 `_docs/SELF_GROWING_EFFECTIVENESS.json` 을 자동 생성한다.
- baseline 비교는 부모 workspace의 현재 `PRODUCT_REVIEW.json` 이 아니라 `REVIEW_HISTORY.json` 의 `parent_job_id` entry를 사용한다.
  - 이유: parent/child가 같은 workspace를 공유하는 경우 부모 `_docs/PRODUCT_REVIEW.json` 이 후속 작업에 의해 덮어써질 수 있기 때문이다.
- [app/product_review_runtime.py](../app/product_review_runtime.py) 는 이제 self-growing effectiveness artifact write callback을 받는다.
- [app/dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 는 job detail용 `self_growing_effectiveness` payload를 반환한다.
  - shared workspace 오탐을 막기 위해 `artifact.job_id != current job_id` 인 경우 mismatch로 간주하고 활성 artifact로 보지 않는다.
- [app/templates/job_detail.html](../app/templates/job_detail.html) workflow 탭에는 `자기 성장 효과` 보드가 추가됐다.
- [app/dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 `runtime.self_growing_effectiveness_summary` 를 반환한다.
  - `improved / unchanged / regressed / insufficient_baseline`
  - `followup_job_count / active_artifact_jobs / missing_artifact_jobs`
  - 최근 follow-up 비교 사례
- [app/templates/index.html](../app/templates/index.html) 운영 요약 탭에는 `자기 성장 효과` 카드가 추가됐다.
- 이번 슬라이스 검증:
  - 타깃 회귀: `28 passed`
  - 전체 회귀: `379 passed, 10 warnings`

### Self-Growing Bridge Long-Term Trend Summary

- [app/dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 `runtime.self_growing_effectiveness_summary` 안에 아래 장기 집계를 함께 반환한다.
  - `latest_generated_day`
  - `recent_timeline`
  - `app_status_breakdown`
- 기준일은 최근 follow-up artifact의 `generated_at` 이고, 최근 7일 동안 `improved / regressed / unchanged / insufficient_baseline` 추세를 일자별로 집계한다.
- 앱별 효과 분포는 `app_code` 기준으로 `개선 / 회귀 / 변화 없음 / 기준 부족 / 개선 비율`을 요약한다.
- [app/templates/index.html](../app/templates/index.html) 의 `자기 성장 효과` 카드는 이제 최근 사례만이 아니라 최근 7일 추세와 앱별 개선/회귀 분포도 함께 보여준다.
- 테스트 fixture에서는 follow-up artifact가 workspace별로 덮어써지지 않도록 서로 다른 repository workspace를 사용해 장기 집계를 검증한다.
- 이번 슬라이스 검증:
  - 타깃 회귀: `20 passed`
  - 전체 회귀: `379 passed, 10 warnings`

### Recurring Failure Cluster Linked Effectiveness

- [app/dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 `failure_pattern_cluster` 기반 backlog candidate와 연결된 follow-up 효과를 별도 facet으로 집계한다.
  - `cluster_linked_followup_count`
  - `cluster_improved_count`
  - `cluster_regressed_count`
  - `cluster_insufficient_baseline_count`
  - `cluster_pattern_counts`
  - `cluster_recent_items`
- 기준은 `SELF_GROWING_EFFECTIVENESS.json.backlog_candidate_id -> memory_backlog_candidates.payload.source_kind == "failure_pattern_cluster"` 매칭이다.
- [app/templates/index.html](../app/templates/index.html) 의 `자기 성장 효과` 카드는 이제 `실패 클러스터 follow-up` 섹션을 함께 보여줘서, 반복 실패 묶음에서 나온 follow-up이 실제로 개선됐는지 바로 본다.
- 최근 사례 목록에도 backlog source kind/title이 같이 붙어, 일반 next-improvement follow-up과 failure cluster follow-up을 구분한다.
- 이번 슬라이스 검증:
  - 타깃 회귀: `20 passed`
  - 전체 회귀: `379 passed, 10 warnings`

### Regressed / Insufficient Baseline Facet

- [app/dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py) 는 이제 `runtime.self_growing_effectiveness_summary` 안에 아래 facet을 함께 반환한다.
  - `regressed_reason_counts`
  - `insufficient_baseline_reasons`
  - `recent_regressed_items`
  - `recent_insufficient_baseline_items`
- 기준은 follow-up artifact의 `status_reasons`, `baseline_missing` 값이며, operator는 회귀 원인과 기준 부족 원인을 분포/최근 사례 기준으로 바로 읽는다.
- [app/templates/index.html](../app/templates/index.html) 의 `자기 성장 효과` 카드는 이제 `회귀 원인 분포`, `기준 부족 분포`, `최근 회귀 사례`, `최근 기준 부족 사례`를 함께 보여준다.
- 관련 회귀 [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)는 새 insufficient baseline fixture를 포함해 장기 집계와 workflow adoption drift까지 같이 고정한다.
- 이번 슬라이스 검증:
  - 타깃 회귀: `20 passed`
  - 전체 회귀: `384 passed, 10 warnings`

### Remaining runtime split: workflow_binding_runtime

- [app/workflow_binding_runtime.py](../app/workflow_binding_runtime.py) 를 추가했다.
- 이 런타임은 아래 책임을 맡는다.
  - workflow node `agent_profile` 해석
  - workflow role binding id 정규화
  - node type별 logical route 목록 해석
  - `role_code / role_preset_id` 기반 route override 계산
  - workflow context의 `issue / paths` guard
- [app/orchestrator.py](../app/orchestrator.py) 는 이제 아래 wrapper만 유지한다.
  - `_workflow_node_agent_profile()`
  - `_normalize_workflow_binding_id()`
  - `_workflow_node_route_names()`
  - `_workflow_node_route_role_overrides()`
  - `_workflow_context_issue()`
  - `_workflow_context_paths()`
- 이 변경으로 workflow node route binding 계산이 오케스트레이터 밖으로 이동했고, 기존 monkeypatch 계약은 wrapper로 유지된다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_workflow_binding_runtime.py](../tests/test_workflow_binding_runtime.py)
  - [test_workflow_node_runtime.py](../tests/test_workflow_node_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)

### Remaining runtime split: job_execution_runtime

- [app/job_execution_runtime.py](../app/job_execution_runtime.py) 를 추가했다.
- 이 런타임은 아래 책임을 맡는다.
  - queue dequeue 후 job dispatch
  - `process_job()`의 track 분기 (`standard / long / ultra / ultra10`)
  - `run_single_attempt()`의 workflow 우선 / fixed pipeline fallback 진입
  - active job/runtime input/heartbeat lifecycle reset
- [app/orchestrator.py](../app/orchestrator.py) 는 이제 아래 wrapper만 유지한다.
  - `process_next_job()`
  - `process_job()`
  - `_process_long_job()`
  - `_process_ultra_job()`
  - `_run_single_attempt()`
- 이 변경으로 queue dispatch와 single-attempt 본문이 오케스트레이터 밖으로 이동했고, 기존 monkeypatch 계약은 wrapper로 유지된다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_job_execution_runtime.py](../tests/test_job_execution_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)
  - [test_manual_workflow_retry.py](../tests/test_manual_workflow_retry.py)
  - [test_worker_recovery.py](../tests/test_worker_recovery.py)

### Remaining runtime split: repository_stage_runtime

- [app/repository_stage_runtime.py](../app/repository_stage_runtime.py) 를 추가했다.
- 이 런타임은 아래 책임을 맡는다.
  - `_docs` 경로 생성 helper
  - 파일 SHA256 helper
  - `git rev-parse --verify` 기반 ref 존재 확인
  - stage 전이 시 store update + actor log 기록
- [app/orchestrator.py](../app/orchestrator.py) 는 이제 아래 wrapper만 유지한다.
  - `_sha256_file()`
  - `_docs_file()`
  - `_ref_exists()`
  - `_set_stage()`
- 이 변경으로 작은 repository/stage 인프라 helper도 오케스트레이터 밖으로 이동했고, 기존 정적 메서드 계약은 wrapper로 유지된다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_repository_stage_runtime.py](../tests/test_repository_stage_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)
  - [test_workspace_repository_runtime.py](../tests/test_workspace_repository_runtime.py)

### Remaining runtime split: orchestrator_context_runtime

- [app/orchestrator_context_runtime.py](../app/orchestrator_context_runtime.py) 를 추가했다.
- 이 런타임은 아래 책임을 맡는다.
  - feature flag 조회
  - lazy memory runtime store 생성
  - runtime input resolve / active env 생성
  - operator input artifact write
  - command template / shell runtime heartbeat + env bridge 설치
- [app/orchestrator.py](../app/orchestrator.py) 는 이제 아래 wrapper만 유지한다.
  - `_install_command_template_heartbeat()`
  - `_feature_enabled()`
  - `_get_memory_runtime_store()`
  - `_resolve_runtime_inputs_for_job()`
  - `_set_active_runtime_input_environment()`
  - `_write_operator_inputs_artifact()`
- 이 변경으로 오케스트레이터 상단의 runtime-input/feature/memory-store/context bridge helper가 더 얇아졌고, `feature_flags_path` 변경도 동적으로 반영된다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_orchestrator_context_runtime.py](../tests/test_orchestrator_context_runtime.py)
  - [test_orchestrator_retry.py](../tests/test_orchestrator_retry.py)
  - [test_job_execution_runtime.py](../tests/test_job_execution_runtime.py)
  - [test_orchestrator_runtime_input_runtime.py](../tests/test_orchestrator_runtime_input_runtime.py)

### Dashboard job action runtime split

- [app/dashboard_job_action_runtime.py](../app/dashboard_job_action_runtime.py) 를 추가했다.
- 아래 dashboard mutation API 본문을 runtime 위임으로 옮겼다.
  - `POST /api/jobs/{job_id}/stop`
  - `POST /api/jobs/{job_id}/requeue`
  - `POST /api/jobs/{job_id}/dead-letter/retry`
  - `POST /api/jobs/{job_id}/workflow/manual-retry`
  - `POST /api/jobs/requeue-failed`
- [app/dashboard.py](../app/dashboard.py) 는 위 경로에서 store/settings helper를 직접 들고 있지 않고 runtime만 호출한다.
- dead-letter retry, manual retry의 recovery trace 계약과 manual workflow retry의 resume validation 계약은 그대로 유지했다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_dashboard_job_action_runtime.py](../tests/test_dashboard_job_action_runtime.py)
  - [test_manual_workflow_retry.py](../tests/test_manual_workflow_retry.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [test_node_runs_api.py](../tests/test_node_runs_api.py)

### 현재 기준

- 현재 라인 수:
  - [app/dashboard.py](../app/dashboard.py): `3656`
  - [app/orchestrator.py](../app/orchestrator.py): `2605`
- 최신 전체 회귀:
  - `420 passed, 10 warnings`
- 현재 다음 우선순위:
  1. `remaining runtime split` 잔여 helper 축소
  2. `enterprise 운영 계층 보강`
  3. `dashboard write action/service 잔여 축소`
  4. 모바일 트랙을 더 이어갈 경우 `MOBILE_E2E_RESULT 기반 blocker / flaky facet`

## 7. 다음 세션 시작 순서

1. [README.md](../README.md)
2. [DOCUMENT_MAP.md](./DOCUMENT_MAP.md)
3. [AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](./AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md)
4. [CURRENT_STATE_GAP_REPORT.md](./CURRENT_STATE_GAP_REPORT.md)
5. 이 문서
6. 관련 대상 파일과 테스트
