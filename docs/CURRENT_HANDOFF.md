# Current Handoff

기준 시각: 2026-03-14 (KST)

- 이 문서의 현재 source-of-truth는 `## 1. 이번 턴까지 완료한 것` 섹션이다.
- 그 아래의 오래된 섹션들은 당시 시점 기록이며, 라인 수/테스트 수/다음 우선순위가 현재와 다를 수 있다.

## 1. 이번 턴까지 완료한 것

### Phase 8-A planner evidence tool loop expansion baseline

- planner role이 이제 `research_search`만이 아니라 `repo_search`, `memory_search`도 요청할 수 있게 열렸다.
  - [config/roles.json](../config/roles.json)
  - [app/dashboard_roles_runtime.py](../app/dashboard_roles_runtime.py)
- [app/prompt_builder.py](../app/prompt_builder.py) 의 planner `TOOL_REQUEST` 규칙도 현재 계약에 맞게 확장했다.
  - `research_search`: 외부 최신 정보/문서 근거
  - `repo_search`: 현재 저장소 코드/파일/심볼 근거
  - `memory_search`: 과거 decision/failure/convention memory 근거
- 현재 상태:
  - planner는 이제 외부 검색만 보지 않고 저장소 내부 evidence와 memory evidence도 능동적으로 끌어올 수 있다.
  - 아직 `log_lookup`까지 planner에 열지는 않았다. 이번 슬라이스는 planning 품질에 직접 필요한 `repo/memory` 경계까지만 연다.
- 다음 우선순위 1~3:
  1. `8-A. graph/subgraph primary-candidate 승격`
  2. `8-C. self-growing signal -> next strategy 입력 강제`
  3. `8-D. graph/subgraph visualization baseline`
- 리스크 / 가정:
  - tool을 더 열었지만 planner prompt가 실제로 어떤 tool을 고를지는 모델 판단에 따른다.
  - 이번 슬라이스는 permission/contract 확장이고, route selection 품질 최적화는 이후 과제다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/prompt_builder.py app/dashboard_roles_runtime.py tests/test_ai_role_routing.py tests/test_jobs_dashboard_api.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_ai_role_routing.py tests/test_jobs_dashboard_api.py -k "route_runtime_context_with_skills_and_tools or default_catalog_hides_legacy_provider_roles"` -> `2 passed, 38 deselected`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_roles_runtime.py` -> `3 passed`

### Phase 8-B first slice: vector retrieval prompt-input rollout baseline

- [app/memory_retrieval_runtime.py](../app/memory_retrieval_runtime.py) 가 이제 `vector_memory_retrieval` 활성 시 `memory_search` tool 뿐 아니라 planner / reviewer / coder용 `MEMORY_CONTEXT.json` 생성에도 Qdrant vector 후보를 섞는다.
  - route별 query를 따로 만들고
  - vector hit가 현재 runtime DB entry와 연결될 때만 context에 올리며
  - 기존 SQLite 기반 selection/context는 fallback으로 유지한다.
- `MEMORY_TRACE.json` 과 `MEMORY_SELECTION.json` 에도 route별 vector 사용 여부를 남기도록 확장했다.
  - `source_counts`
  - `vector_selected_count`
  - `vector_routes`
- feature flag/operator 설명도 현재 동작에 맞게 갱신했다.
  - [app/feature_flags.py](../app/feature_flags.py)
  - [app/dashboard_admin_metrics_runtime.py](../app/dashboard_admin_metrics_runtime.py)
- 현재 상태:
  - vector retrieval은 이제 `memory_search only`가 아니라 prompt-input baseline까지 올라왔다.
  - 여전히 `opt-in`이며, Qdrant 미구성/실패/no match면 기존 DB selection으로 자동 fallback 한다.
  - 이번 슬라이스는 planner/reviewer/coder prompt injection baseline까지이며, planner/recovery graph promotion이나 long-horizon self-growing 변경은 아직 아니다.
- 다음 우선순위 1~3:
  1. `8-E. remaining runtime split / read-service long-tail`
  2. `8-A. planner / recovery / diagnosis graph-subgraph primary-candidate 승격`
  3. `8-C. self-growing signal -> next strategy 입력 강제`
- 리스크 / 가정:
  - vector candidate는 현재 runtime DB에 매핑되는 memory만 prompt context에 올린다. stale shadow hit는 의도적으로 무시한다.
  - route query는 현재 `issue_title + route hint` 기반 heuristic 이므로, 이후 query builder 고도화 여지가 있다.
  - 이번 슬라이스는 opt-in baseline이라 기본 동작은 그대로다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/memory_retrieval_runtime.py app/feature_flags.py app/dashboard_admin_metrics_runtime.py tests/test_memory_retrieval_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_memory_retrieval_runtime.py tests/test_orchestrator_retry.py -k "memory_retrieval"` -> `9 passed, 41 deselected`
  - `.venv/bin/python -m pytest -q tests/test_tool_runtime.py tests/test_tool_support_runtime.py -k "vector_memory_retrieval or memory_search or vector"` -> `5 passed, 10 deselected`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py tests/test_dashboard_settings_runtime.py tests/test_jobs_dashboard_api.py -k "vector_memory_retrieval or feature_flags or admin_metrics"` -> `4 passed, 50 deselected`
  - `.venv/bin/python -m pytest -q` -> `548 passed, 10 warnings`

### Roadmap rephase: Phase 8 nonlinear engine closure / Phase 9 HA gate

- [docs/PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md](./PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md) 를 추가해 Phase 8을 `비선형성 / vector retrieval / graph-subgraph / self-growing strong closure` phase로 재정의했다.
- 상위 source-of-truth 문서도 같은 방향으로 맞췄다.
  - [docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](./AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md)
  - [docs/GOAL_CLOSURE_PRIORITY_RESET.md](./GOAL_CLOSURE_PRIORITY_RESET.md)
  - [docs/PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md](./PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md)
  - [docs/DOCUMENT_MAP.md](./DOCUMENT_MAP.md)
  - [README.md](../README.md)
- 이번 재배치의 핵심 원칙:
  - `Phase 8`은 Phase 4를 다시 시작하는 단계가 아니라, 이미 partial/shadow/opt-in 인 엔진 요소를 strong으로 승격하는 단계다.
  - 남은 Phase 7 항목인 `remaining runtime split / read-service long-tail`, `durable backend`, `self-check alert provider policy hardening` 은 `8-E. Phase 7 Carry-Over Enabling Track`으로 넘겨 관리한다.
  - `Phase 9`는 그 뒤에 오는 `Zero-Downtime / HA` 전용 phase로 고정한다.
- 현재 상태:
  - 상위 로드맵의 `Delivery Order` 는 이제 `7 -> 8(engine closure) -> 9(HA)` 순서다.
  - goal-reset 문서도 `비선형 runtime / vector / graph-subgraph / self-growing strong closure` 를 핵심 필수로 올렸다.
  - Phase 7 문서는 baseline을 사실상 닫은 뒤 남은 blocker를 Phase 8 enabling track으로 넘긴 상태로 정리됐다.
- 다음 우선순위 1~3:
  1. `8-E. Phase 7 Carry-Over Enabling Track`
  2. `8-A. Nonlinear Runtime Promotion`
  3. `8-B. Vector Retrieval Promotion`
- 리스크 / 가정:
  - 이번 턴은 문서 재배치이며, 코드 실행 순서나 runtime behavior를 직접 바꾸지는 않았다.
  - `operator-facing graph/subgraph visualization baseline` 은 Phase 8에 포함되지만, `full visual editor / drag-and-drop builder` 까지 이번 우선순위로 올린 것은 아니다.
  - mobile emulator/E2E strong, integration/operator control strong 같은 기존 핵심 항목은 삭제가 아니라 상대 우선순위 조정이다.
  - 현재 worktree 기준 [docs/GOAL_CLOSURE_PRIORITY_RESET.md](./GOAL_CLOSURE_PRIORITY_RESET.md), [docs/PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md](./PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md), [docs/PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md](./PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md) 는 신규/untracked 문서 상태이므로, 이후 커밋 단계에서 같이 포함해야 한다.
- 검증 결과:
  - 문서 상호참조와 phase 정의를 수동 점검했다.
  - 코드/테스트 실행은 하지 않았다. 이번 슬라이스는 docs-only 변경이다.

### Durable self-check alert exponential backoff baseline

- [app/self_check_alert_delivery_runtime.py](../app/self_check_alert_delivery_runtime.py) 에 route 실패 누적 기준 exponential backoff를 추가했다.
  - 같은 fingerprint 재전송은 기존 `repeat_minutes` cooldown을 유지한다.
  - 같은 route가 연속 실패하면 재시도 간격은 `repeat -> 2x -> 4x` 식으로 커지고 `failure_backoff_max_minutes` 상한에서 멈춘다.
  - delivery payload 는 `effective_repeat_minutes`, `consecutive_failure_count`, `backoff_active`, `failure_backoff_max_minutes` 를 함께 반환한다.
- [app/config.py](../app/config.py), [app/self_check_main.py](../app/self_check_main.py), [app/dashboard_builder_runtime.py](../app/dashboard_builder_runtime.py) 는 새 설정 `AGENTHUB_SELF_CHECK_ALERT_FAILURE_BACKOFF_MAX_MINUTES` 를 연결했다.
- [app/templates/index.html](../app/templates/index.html) 의 `Periodic Self-Check` detail 은 현재 retry window, max backoff, 연속 실패 수를 같이 보여준다.
- 운영 설정과 hygiene 계약도 같이 갱신했다.
  - [.env.example](../.env.example)
  - [scripts/setup_local_config.sh](../scripts/setup_local_config.sh)
  - [scripts/check_repo_hygiene.py](../scripts/check_repo_hygiene.py)
  - [tests/test_setup_local_config_script.py](../tests/test_setup_local_config_script.py)
  - [tests/test_repo_hygiene.py](../tests/test_repo_hygiene.py)
- 관련 핵심 계약:
  - [tests/test_self_check_alert_delivery_runtime.py](../tests/test_self_check_alert_delivery_runtime.py)
  - [tests/test_durable_runtime_self_check.py](../tests/test_durable_runtime_self_check.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - self-check alert delivery 는 이제 primary/critical route 상태뿐 아니라 route별 backoff 상태까지 persisted payload로 남긴다.
  - acknowledged alert 는 계속 재전송하지 않고 idle route로 남으며, open 상태의 반복 실패만 backoff 대상이다.
  - operator는 대시보드에서 현재 적용 중인 retry window와 연속 실패 수를 바로 읽을 수 있다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert provider policy hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 exponential backoff 상한까지만 넣었고, provider별 jitter, route disable, dead target quarantine은 아직 없다.
  - current backoff는 route별 persisted state 기준이므로 delivery file 삭제 시 failure streak도 같이 초기화된다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/self_check_alert_delivery_runtime.py app/config.py app/self_check_main.py app/dashboard_builder_runtime.py app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_self_check_alert_delivery_runtime.py tests/test_durable_runtime_self_check.py` -> `14 passed`
  - `.venv/bin/python -m pytest -q tests/test_setup_local_config_script.py tests/test_repo_hygiene.py` -> `4 passed`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "durable_runtime_self_check or security_governance or patch_updater_status"` -> `5 passed, 28 deselected`

### Dashboard builder compatibility split

- [app/dashboard_builder_runtime.py](../app/dashboard_builder_runtime.py) 를 추가했다.
  - `_build_dashboard_*`, `_build_patch_*`, `_build_durable_*` 계열 builder 본문을 이 파일로 옮겼다.
  - `_job_workspace_path`, `_memory_runtime_db_path`, `_get_memory_runtime_store`, `_classify_command_target`, `_run_gh_command`, `_read_registered_apps` 같은 helper 본문도 같이 옮겼다.
- [app/dashboard.py](../app/dashboard.py) 는 이제 route 조립, config constant, monkeypatch-friendly compatibility wrapper 중심만 남긴다.
  - 기존 테스트가 기대하는 `app.dashboard` import surface를 유지하기 위해 `collect_agent_cli_status`, `load_workflows`, `feature_flags_payload`, `utc_now_iso` 같은 상단 import surface도 다시 노출한다.
  - 새 builder runtime은 `app.dashboard` 를 lazy import 해서 patched wrapper/constant를 다시 참조한다.
- 관련 핵심 계약:
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
  - [tests/test_assistant_chat.py](../tests/test_assistant_chat.py)
  - [tests/test_assistant_log_analysis.py](../tests/test_assistant_log_analysis.py)
  - [tests/test_dashboard_job_action_runtime.py](../tests/test_dashboard_job_action_runtime.py)
  - [tests/test_dashboard_job_artifact_runtime.py](../tests/test_dashboard_job_artifact_runtime.py)
  - [tests/test_dashboard_view_runtime.py](../tests/test_dashboard_view_runtime.py)
  - [tests/test_dashboard_assistant_diagnosis_runtime.py](../tests/test_dashboard_assistant_diagnosis_runtime.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `792 -> 452` lines 로 줄었다.
  - builder 구현은 [app/dashboard_builder_runtime.py](../app/dashboard_builder_runtime.py) `690` lines 로 이동했고, `dashboard.py` 는 compatibility wrapper 파일로 성격이 더 분명해졌다.
  - route는 이미 분리된 [app/dashboard_job_router.py](../app/dashboard_job_router.py), [app/dashboard_write_router.py](../app/dashboard_write_router.py), [app/dashboard_operator_router.py](../app/dashboard_operator_router.py), [app/dashboard_config_router.py](../app/dashboard_config_router.py) 에 남아 있다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 새 builder runtime은 compatibility를 위해 `app.dashboard` lazy import를 사용하므로, 앞으로 builder contract를 바꿀 때는 [app/dashboard.py](../app/dashboard.py) wrapper surface와 같이 봐야 한다.
  - `dashboard.py` 는 얇아졌지만 여전히 monkeypatch contract용 public shim 역할을 하므로, helper 이름 변경은 테스트/라우터 lazy import 경계를 같이 깨뜨릴 수 있다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_builder_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_assistant_chat.py tests/test_assistant_log_analysis.py tests/test_jobs_dashboard_api.py tests/test_node_runs_api.py tests/test_workflow_settings_api.py` -> `82 passed`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_action_runtime.py tests/test_dashboard_job_artifact_runtime.py tests/test_dashboard_view_runtime.py tests/test_dashboard_assistant_diagnosis_runtime.py` -> `13 passed`

### Dashboard jobs/admin-metrics/view router split

- [app/dashboard_job_router.py](../app/dashboard_job_router.py) 를 추가했다.
  - `GET /`
  - `GET /api/jobs`
  - `GET /api/admin/metrics`
  - `GET /api/jobs/options`
  - `GET /jobs/{job_id}`
  - `GET /api/jobs/{job_id}`
  - `GET /api/jobs/{job_id}/node-runs`
  - `POST /api/jobs/{job_id}/stop`
  - `POST /api/jobs/{job_id}/requeue`
  - `POST /api/jobs/{job_id}/dead-letter/retry`
  - `POST /api/jobs/{job_id}/workflow/manual-retry`
  - `POST /api/jobs/requeue-failed`
  - `GET /logs/{file_name}`
  job/view/admin-metrics route를 이 서브라우터로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 에서는 관련 payload model과 route wrapper를 제거하고 `router.include_router(...)`만 남겼다.
- 새 route 구현은 `app.dashboard` lazy import 후 기존 `_build_dashboard_job_list_runtime`, `_build_dashboard_admin_metrics_runtime`, `_build_dashboard_job_detail_runtime`, `_build_dashboard_job_action_runtime`, `_build_dashboard_view_runtime` builder를 그대로 사용한다.
- 관련 핵심 계약:
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)
  - [tests/test_log_route_security.py](../tests/test_log_route_security.py)
  - [tests/test_dashboard_job_list_runtime.py](../tests/test_dashboard_job_list_runtime.py)
  - [tests/test_dashboard_job_detail_runtime.py](../tests/test_dashboard_job_detail_runtime.py)
  - [tests/test_dashboard_job_action_runtime.py](../tests/test_dashboard_job_action_runtime.py)
  - [tests/test_dashboard_view_runtime.py](../tests/test_dashboard_view_runtime.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `995 -> 792` lines 로 줄었다.
  - jobs/admin-metrics/page-log/action API는 기존 경로/응답 계약을 유지하면서 [app/dashboard_job_router.py](../app/dashboard_job_router.py) 에서 처리한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 job/view route grouping 분리까지이며, `dashboard.py` 에는 builder/helper와 compatibility wrapper가 아직 남아 있다.
  - route-level monkeypatch가 필요하면 `app.dashboard_job_router` 와 `app.dashboard` lazy import 경계를 같이 봐야 한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_job_router.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py tests/test_node_runs_api.py tests/test_log_route_security.py -k "jobs_api or job_options_api or admin_metrics or job_detail_api or node_runs or dead_letter or manual_retry or requeue or log_route or job_detail_page"` -> `33 passed, 26 deselected`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_list_runtime.py tests/test_dashboard_job_detail_runtime.py tests/test_dashboard_job_action_runtime.py tests/test_dashboard_view_runtime.py` -> `13 passed`

### Dashboard apps/assistant/issue write router split

- [app/dashboard_write_router.py](../app/dashboard_write_router.py) 를 추가했다.
  - `GET/POST/DELETE /api/apps*`
  - `POST /api/assistant/codex-chat`
  - `POST /api/assistant/chat`
  - `POST /api/assistant/log-analysis`
  - `POST /api/issues/register`
  write-oriented dashboard route를 이 서브라우터로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 에서는 관련 payload model과 route wrapper를 제거하고 `router.include_router(...)`만 남겼다.
- 새 route 구현은 `app.dashboard` lazy import 후 기존 `_build_dashboard_app_registry_runtime`, `_build_dashboard_assistant_runtime`, `_build_dashboard_issue_registration_runtime` builder를 그대로 사용해 monkeypatch 기반 테스트 계약을 유지한다.
- 관련 핵심 계약:
  - [tests/test_assistant_chat.py](../tests/test_assistant_chat.py)
  - [tests/test_assistant_log_analysis.py](../tests/test_assistant_log_analysis.py)
  - [tests/test_dashboard_app_registry_runtime.py](../tests/test_dashboard_app_registry_runtime.py)
  - [tests/test_dashboard_issue_registration_runtime.py](../tests/test_dashboard_issue_registration_runtime.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `1140 -> 995` lines 로 줄었다.
  - apps/assistant/issue registration API는 기존 경로/응답 계약을 유지하면서 [app/dashboard_write_router.py](../app/dashboard_write_router.py) 에서 처리한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 write-oriented route grouping 분리까지이며, job detail/action/view route와 일부 builder/helper는 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - builder monkeypatch 계약은 유지했지만, route-level monkeypatch가 필요하면 `app.dashboard_write_router` 와 `app.dashboard` lazy import 경계를 같이 봐야 한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_write_router.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_assistant_chat.py tests/test_assistant_log_analysis.py tests/test_dashboard_app_registry_runtime.py tests/test_dashboard_issue_registration_runtime.py` -> `18 passed`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py -k "issue_register or upsert_app"` -> `5 passed, 10 deselected`

### Dashboard operator patch/memory/self-check router expansion

- [app/dashboard_operator_router.py](../app/dashboard_operator_router.py) 에 아래 operator/admin route 묶음을 추가로 이동했다.
  - `GET /api/admin/patch-status`
  - `GET /api/admin/patch-runs/latest`
  - `GET /api/admin/patch-updater-status`
  - `GET /api/admin/security-governance`
  - `GET/POST /api/admin/durable-runtime-hygiene*`
  - `GET/POST /api/admin/durable-runtime-self-check*`
  - `POST /api/admin/patch-runs*`
  - `GET/POST /api/admin/memory*`
- [app/dashboard.py](../app/dashboard.py) 에서는 관련 payload model과 route wrapper를 제거했다.
- 새 route 구현은 `app.dashboard` lazy import 후 기존 `_build_patch_control_runtime`, `_build_dashboard_patch_runtime`, `_build_durable_runtime_self_check_runtime`, `_build_dashboard_memory_admin_runtime` builder를 그대로 사용해 monkeypatch 기반 테스트 계약을 유지한다.
- 관련 핵심 계약:
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_durable_runtime_self_check.py](../tests/test_durable_runtime_self_check.py)
  - [tests/test_dashboard_memory_admin_runtime.py](../tests/test_dashboard_memory_admin_runtime.py)
  - [tests/test_dashboard_patch_runtime.py](../tests/test_dashboard_patch_runtime.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `1424 -> 1140` lines 로 줄었다.
  - operator-facing patch/hygiene/self-check/memory admin API는 기존 경로/응답 계약을 유지하면서 [app/dashboard_operator_router.py](../app/dashboard_operator_router.py) 에서 처리한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 operator route grouping 확장까지이며, app registry, assistant, issue registration, job detail/action route는 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - builder monkeypatch 계약은 유지했지만, route-level monkeypatch가 필요하면 `app.dashboard_operator_router` 와 `app.dashboard` lazy import 경계를 같이 봐야 한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_operator_router.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "patch_status or patch_run or patch_updater_status or security_governance or durable_runtime_hygiene or durable_runtime_self_check or admin_memory"` -> `16 passed, 17 deselected`
  - `.venv/bin/python -m pytest -q tests/test_durable_runtime_self_check.py tests/test_dashboard_memory_admin_runtime.py tests/test_dashboard_patch_runtime.py` -> `21 passed`

### Dashboard roles/settings config router split

- [app/dashboard_config_router.py](../app/dashboard_config_router.py) 를 추가했다.
  - `GET/POST/DELETE /api/roles*`
  - `GET/POST /api/workflows*`
  - `GET/POST /api/feature-flags`
  - `GET/POST /api/agents/config`
  - `GET /api/agents/check`
  - `GET /api/agents/models`
  config-oriented dashboard API를 이 서브라우터로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 에서는 관련 payload model과 route wrapper를 제거하고 `router.include_router(...)`만 남겼다.
- 새 라우터는 호출 시점에 `app.dashboard` 를 lazy import 해서 `_ROLES_CONFIG_PATH`, `_WORKFLOWS_CONFIG_PATH`, `_FEATURE_FLAGS_CONFIG_PATH` monkeypatch 계약을 그대로 유지한다.
- 관련 핵심 계약:
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_dashboard_settings_runtime.py](../tests/test_dashboard_settings_runtime.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `1656 -> 1424` lines 로 줄었다.
  - roles/workflows/feature-flags/agent-config API는 기존 경로/응답 계약을 유지하면서 [app/dashboard_config_router.py](../app/dashboard_config_router.py) 에서 처리한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 config-oriented route grouping 분리까지이며, assistant, issue registration, app registry, admin patch/memory route는 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - config path override 테스트는 유지되지만, 이후 route-level monkeypatch가 필요하면 `app.dashboard_config_router` 와 `app.dashboard` 의 lazy import 경계를 같이 봐야 한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_config_router.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py` -> `15 passed`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py tests/test_jobs_dashboard_api.py -k "roles_api or feature_flags or workflows or agent_models or agent_cli_check_api_returns_git_and_gh"` -> `6 passed, 42 deselected`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_settings_runtime.py` -> `6 passed`

### Dashboard operator runtime-input / integration router split

- [app/dashboard_operator_router.py](../app/dashboard_operator_router.py) 를 추가했다.
  - `GET/POST /api/admin/runtime-inputs*`
  - `GET/POST /api/admin/integrations*`
  operator-side admin API를 이 서브라우터로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 에서는 관련 payload model, route wrapper, builder를 제거하고 `router.include_router(...)`만 남겼다.
- 목적은 `remaining runtime split / read-service long-tail` 다음 조각으로, 내부 runtime은 이미 분리된 상태에서 `dashboard.py` 에 남아 있던 operator admin route surface를 줄이는 것이다.
- 관련 핵심 계약:
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_dashboard_admin_metrics_runtime.py](../tests/test_dashboard_admin_metrics_runtime.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `1894 -> 1656` lines 로 줄었다.
  - runtime input / integration CRUD는 기존 경로와 응답 형태를 유지하면서 [app/dashboard_operator_router.py](../app/dashboard_operator_router.py) 에서 처리한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 operator admin route grouping 분리까지이며, roles/admin patch route와 일부 builder는 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - API path/response contract는 유지했지만, route 정의 위치가 바뀌었으므로 이후 monkeypatch가 필요하면 `app.dashboard_operator_router` 쪽을 봐야 한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_operator_router.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "runtime_input or integrations or admin_metrics"` -> `6 passed, 27 deselected`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_admin_metrics_runtime.py tests/test_jobs_dashboard_api.py -k "runtime_input or integrations or admin_metrics"` -> `7 passed, 27 deselected`

### Dashboard compatibility helper runtime split

- [app/dashboard_compat_runtime.py](../app/dashboard_compat_runtime.py) 를 추가했다.
  - assistant dispatch helper
  - provider alias compatibility wrapper
  - GitHub CLI / label / repository normalization helper
  - app/workflow config IO helper
  구현 본문을 이 파일로 모았다.
- [app/dashboard.py](../app/dashboard.py) 는 `_run_log_analyzer`, `_run_assistant_chat_provider`, `_run_codex_log_analysis`, `_run_copilot_log_analysis`, `_run_gh_command`, `_ensure_label`, `_read_registered_apps` 같은 dashboard-local compatibility helper에서 이제 thin pass-through만 남기고 새 runtime으로 위임한다.
- 목적은 `remaining runtime split / read-service long-tail` 다음 조각으로, route 테스트가 기대하는 monkeypatch 포인트는 유지하면서 `dashboard.py` 하단 helper implementation 의존을 더 줄이는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_compat_runtime.py](../tests/test_dashboard_compat_runtime.py)
  - [tests/test_dashboard_assistant_runtime.py](../tests/test_dashboard_assistant_runtime.py)
  - [tests/test_dashboard_app_registry_runtime.py](../tests/test_dashboard_app_registry_runtime.py)
  - [tests/test_dashboard_issue_registration_runtime.py](../tests/test_dashboard_issue_registration_runtime.py)
  - [tests/test_assistant_chat.py](../tests/test_assistant_chat.py)
  - [tests/test_assistant_log_analysis.py](../tests/test_assistant_log_analysis.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `1897 -> 1894` lines 로 줄었다.
  - assistant/GitHub/app-config helper 본문은 [app/dashboard_compat_runtime.py](../app/dashboard_compat_runtime.py) 로 이동했고, dashboard module monkeypatch 계약은 그대로 유지한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - `_run_log_analyzer`, `_run_assistant_chat_provider` 는 테스트에서 provider wrapper monkeypatch를 사용하므로 완전 제거하지 않고 pass-through로 유지했다.
  - `dashboard.py` 에는 아직 roles/runtime-input/integration/admin patch 관련 thin builder/route surface가 남아 있다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_compat_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_compat_runtime.py tests/test_dashboard_assistant_runtime.py tests/test_dashboard_app_registry_runtime.py tests/test_dashboard_issue_registration_runtime.py` -> `15 passed`
  - `.venv/bin/python -m pytest -q tests/test_assistant_chat.py tests/test_assistant_log_analysis.py tests/test_workflow_settings_api.py -k "issue_register or upsert_app or assistant/chat or log_analysis"` -> `11 passed, 14 deselected`

### Dashboard dead wrapper cleanup after runtime split

- [app/dashboard.py](../app/dashboard.py) 에 남아 있던 unused dashboard-local helper를 정리했다.
  - `_build_job_runtime_signals`
  - `_list_dashboard_jobs`
  - `_build_job_summary`
  - `_filter_dashboard_jobs`
  - `_paginate_dashboard_jobs`
  - `_dashboard_filter_options`
  는 더 이상 참조되지 않아 제거했다.
- [app/dashboard.py](../app/dashboard.py) 의 admin metrics builder는 삭제한 wrapper를 다시 두지 않고 runtime/static method를 직접 주입하도록 바꿨다.
  - `DashboardJobListRuntime.list_dashboard_jobs()`
  - `DashboardJobListRuntime.build_job_summary()`
- 목적은 `remaining runtime split / read-service long-tail` 다음 조각으로, runtime split 이후 남은 dead local surface를 걷어 `dashboard.py` 잔여를 더 줄이는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_job_list_runtime.py](../tests/test_dashboard_job_list_runtime.py)
  - [tests/test_dashboard_view_runtime.py](../tests/test_dashboard_view_runtime.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `1959 -> 1897` lines 로 줄었다.
  - admin metrics / jobs / log / page read path는 기존 계약을 유지한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 dead helper cleanup 이며, route grouping 자체나 assistant/GitHub compatibility wrapper는 그대로 남겨뒀다.
  - `dashboard.py` 는 더 얇아졌지만 아직 roles/runtime-input/integration/admin patch wrapper가 일부 남아 있다.
- 검증 결과:
  - `rg -n "_build_job_runtime_signals\\(|_list_dashboard_jobs\\(|_build_job_summary\\(|_filter_dashboard_jobs\\(|_paginate_dashboard_jobs\\(|_dashboard_filter_options\\(" app tests` -> no matches
  - `.venv/bin/python -m py_compile app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_list_runtime.py tests/test_dashboard_view_runtime.py tests/test_jobs_dashboard_api.py -k "jobs_api or job_options_api or job_detail_page or log_route or admin_metrics"` -> `8 passed, 32 deselected`

### Durable self-check critical escalation route baseline

- [app/self_check_alert_delivery_runtime.py](../app/self_check_alert_delivery_runtime.py) 에 route-aware delivery 상태를 붙였다.
  - 기존 primary webhook delivery baseline은 유지한다.
  - `critical` severity alert 일 때만 추가 target인 `critical escalation` route 를 활성화한다.
  - persisted delivery payload 는 이제 route별 상태(`routes[]`), active route 수, partial failure 상태를 함께 담는다.
- 설정 경계도 같이 확장했다.
  - [app/config.py](../app/config.py)
  - [.env.example](../.env.example)
  - [scripts/setup_local_config.sh](../scripts/setup_local_config.sh)
  - [scripts/check_repo_hygiene.py](../scripts/check_repo_hygiene.py)
  - 새 ENV: `AGENTHUB_SELF_CHECK_ALERT_CRITICAL_WEBHOOK_URL`
- [app/dashboard.py](../app/dashboard.py), [app/self_check_main.py](../app/self_check_main.py) 는 이제 critical escalation route 설정까지 포함한 delivery runtime builder를 사용한다.
- [app/templates/index.html](../app/templates/index.html) 의 `Periodic Self-Check` detail 은 route count, active route count, route별 status/target 을 같이 보여준다.
- 관련 핵심 계약:
  - [tests/test_self_check_alert_delivery_runtime.py](../tests/test_self_check_alert_delivery_runtime.py)
  - [tests/test_durable_runtime_self_check.py](../tests/test_durable_runtime_self_check.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_setup_local_config_script.py](../tests/test_setup_local_config_script.py)
  - [tests/test_repo_hygiene.py](../tests/test_repo_hygiene.py)
- 현재 상태:
  - self-check alert delivery 는 이제 primary route + optional critical escalation route 까지 지원한다.
  - 같은 alert fingerprint 는 route별 cooldown 이후에만 다시 전송한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert escalation policy / backoff hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - current escalation route 는 `critical` severity 기준의 정적 추가 target 이며, ack-aware suppression, exponential backoff, provider별 재시도 정책은 아직 없다.
  - primary URL과 critical URL이 같으면 duplicate send는 피하고 primary route만 유지한다.
  - dashboard visibility는 route summary를 보여주지만, 개별 route 재전송/disable action은 아직 없다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/self_check_alert_delivery_runtime.py app/dashboard.py app/self_check_main.py app/config.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_self_check_alert_delivery_runtime.py` -> `6 passed`
  - `.venv/bin/python -m pytest -q tests/test_setup_local_config_script.py tests/test_repo_hygiene.py` -> `4 passed`
  - `.venv/bin/python -m pytest -q tests/test_durable_runtime_self_check.py tests/test_jobs_dashboard_api.py -k "durable_runtime_self_check"` -> `9 passed, 30 deselected`

### Dashboard page / log view runtime split

- [app/dashboard_view_runtime.py](../app/dashboard_view_runtime.py) 를 추가했다.
  - dashboard shell render
  - job detail HTML shell render
  - plain-text log file read/validation
  - stop signal path helper
  를 route 밖으로 옮겼다.
- [app/dashboard.py](../app/dashboard.py) 는 이제
  - `GET /`
  - `GET /jobs/{job_id}`
  - `GET /logs/{file_name}`
  에서 thin wrapper만 남기고 [app/dashboard_view_runtime.py](../app/dashboard_view_runtime.py) 로 위임한다.
- 목적은 `remaining runtime split / read-service long-tail` 의 다음 조각으로, 아직 route 안에 남아 있던 HTML shell / log-serving read surface를 정리하는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_view_runtime.py](../tests/test_dashboard_view_runtime.py)
  - [tests/test_log_route_security.py](../tests/test_log_route_security.py)
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `1967 -> 1958` lines 로 줄었다.
  - `job_detail.html` render는 request-first `TemplateResponse` 형태로 통일됐다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert routing / escalation hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 view/log surface 분리까지만 다루며, assistant monkeypatch wrapper와 workflow/app helper thin wrapper는 그대로 남겨뒀다.
  - `/logs/*` route는 기존과 동일하게 filename allow-list 검증 후 UTF-8 text를 그대로 반환한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard_view_runtime.py app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_view_runtime.py tests/test_log_route_security.py tests/test_node_runs_api.py -k "job_detail_page or log_route"` -> `5 passed, 25 deselected`

### Durable self-check alert webhook delivery baseline

- [app/self_check_alert_delivery_runtime.py](../app/self_check_alert_delivery_runtime.py) 를 추가했다.
  - self-check alert webhook delivery 상태를 별도 runtime/persisted payload로 관리한다.
  - 같은 alert fingerprint는 `AGENTHUB_SELF_CHECK_ALERT_REPEAT_MINUTES` 기준 cooldown 이후에만 재전송한다.
  - 최신 delivery state는 [app/config.py](../app/config.py) 의 `durable_runtime_self_check_alert_delivery_file` 경로인 `data/durable_runtime_self_check_alert_delivery.json` 에 저장된다.
- [app/durable_runtime_self_check.py](../app/durable_runtime_self_check.py) 는 이제 alert lifecycle 외에 delivery payload도 함께 반환한다.
  - `read_status()` 는 현재 alert/report 기준 delivery visibility를 붙인다.
  - `run_check()` 는 alert state 갱신 후 webhook delivery를 시도하고 결과를 payload에 포함한다.
  - `acknowledge_alert()` 응답도 acknowledged alert 기준 delivery 상태를 다시 계산한다.
- [app/dashboard.py](../app/dashboard.py), [app/self_check_main.py](../app/self_check_main.py) 는 새 delivery runtime을 builder로 연결했다.
  - dashboard/self-check timer 모두 같은 delivery state file을 사용한다.
  - [app/templates/index.html](../app/templates/index.html) 의 `Periodic Self-Check` 카드는 이제 delivery 상태, target, last attempt, next due, error를 같이 보여준다.
- 운영 설정/문서도 현재 기준으로 맞췄다.
  - [.env.example](../.env.example)
  - [scripts/setup_local_config.sh](../scripts/setup_local_config.sh)
  - [scripts/check_repo_hygiene.py](../scripts/check_repo_hygiene.py)
  - [README.md](../README.md)
  - [docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](./AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md)
  - [docs/PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md](./PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md)
- 관련 핵심 계약:
  - [tests/test_self_check_alert_delivery_runtime.py](../tests/test_self_check_alert_delivery_runtime.py)
  - [tests/test_durable_runtime_self_check.py](../tests/test_durable_runtime_self_check.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_setup_local_config_script.py](../tests/test_setup_local_config_script.py)
  - [tests/test_repo_hygiene.py](../tests/test_repo_hygiene.py)
- 현재 상태:
  - self-check baseline은 이제 report/alert lifecycle에 더해 optional webhook delivery state까지 함께 유지한다.
  - outbound target은 단일 webhook baseline이며, multi-target routing/escalation policy는 아직 없다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert routing / escalation hardening`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - webhook delivery는 `AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_URL` 이 비어 있으면 자동으로 비활성화된다.
  - 같은 alert fingerprint 판단은 warning code set 기준이므로, code가 같고 메시지 세부만 바뀐 경우 같은 alert로 간주한다.
  - 현재 delivery runtime은 인증 헤더/다중 타깃/백오프 정책 없이 단일 JSON webhook POST baseline만 제공한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/self_check_alert_delivery_runtime.py app/durable_runtime_self_check.py app/self_check_main.py app/dashboard.py app/config.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_self_check_alert_delivery_runtime.py tests/test_durable_runtime_self_check.py` -> `10 passed`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "durable_runtime_self_check"` -> `3 passed, 30 deselected`
  - `.venv/bin/python -m pytest -q tests/test_setup_local_config_script.py tests/test_repo_hygiene.py` -> `4 passed`

### Dashboard GitHub CLI helper runtime split

- [app/dashboard_github_cli_runtime.py](../app/dashboard_github_cli_runtime.py) 를 추가했다.
  - `normalize_repository_ref`
  - `extract_issue_url`
  - `extract_issue_number`
  - `run_gh_command`
  - `ensure_label`
  - `ensure_agent_run_label`
  를 통해 dashboard-originated GitHub CLI / label / repository ref helper 본문을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 는 기존 monkeypatch 포인트인 `_run_gh_command`, `_ensure_label`, `_ensure_agent_run_label`, `_extract_issue_url`, `_extract_issue_number`, `_normalize_repository_ref` 를 thin wrapper로만 유지하고, 본문은 새 runtime으로 위임한다.
- 목적은 `remaining runtime split / read-service long-tail` 다음 조각으로, issue registration / app registry 가 공유하던 GitHub helper를 dashboard 본문에서 더 걷어내는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_github_cli_runtime.py](../tests/test_dashboard_github_cli_runtime.py)
  - [tests/test_dashboard_issue_registration_runtime.py](../tests/test_dashboard_issue_registration_runtime.py)
  - [tests/test_dashboard_app_registry_runtime.py](../tests/test_dashboard_app_registry_runtime.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `2022 -> 1942` lines 로 줄었다.
  - dashboard route 테스트/monkeypatch compatibility 포인트는 유지했다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert automation 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 현재 `github_webhook.py` 는 여전히 자체 branch/track helper를 갖고 있고, 이번 슬라이스는 dashboard-originated GitHub helper만 공통화했다.
  - wrapper를 남겨 호환성은 유지했지만, 완전한 helper 제거까지는 아직 한 단계 더 남아 있다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_github_cli_runtime.py app/dashboard_issue_registration_runtime.py app/dashboard_app_registry_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_github_cli_runtime.py tests/test_dashboard_issue_registration_runtime.py tests/test_dashboard_app_registry_runtime.py` -> `12 passed`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py -k "issue_register or upsert_app"` -> `5 passed, 10 deselected`

### Dashboard enqueue support runtime split

- [app/dashboard_job_enqueue_runtime.py](../app/dashboard_job_enqueue_runtime.py) 를 추가했다.
  - `normalize_app_code`
  - `normalize_track`
  - `detect_title_track`
  - `build_branch_name`
  - `build_log_file_name`
  - `find_active_job`
  - `queue_followup_job_from_backlog_candidate`
  를 통해 dashboard-originated issue/backlog enqueue naming/helper 본문을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 는 이제 [app/dashboard_issue_registration_runtime.py](../app/dashboard_issue_registration_runtime.py), [app/dashboard_memory_admin_runtime.py](../app/dashboard_memory_admin_runtime.py), [app/dashboard_app_registry_runtime.py](../app/dashboard_app_registry_runtime.py) builder에서 이 runtime 또는 static helper를 재사용한다.
- 목적은 `remaining runtime split / read-service long-tail` 다음 조각으로, route에 직접 남아 있던 enqueue/follow-up support helper를 걷어 `dashboard.py` 를 더 얇게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_job_enqueue_runtime.py](../tests/test_dashboard_job_enqueue_runtime.py)
  - [tests/test_dashboard_issue_registration_runtime.py](../tests/test_dashboard_issue_registration_runtime.py)
  - [tests/test_dashboard_memory_admin_runtime.py](../tests/test_dashboard_memory_admin_runtime.py)
  - [tests/test_dashboard_app_registry_runtime.py](../tests/test_dashboard_app_registry_runtime.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `2285 -> 2022` lines 로 줄었다.
  - shared branch/log/follow-up queue 규칙은 이제 `dashboard.py` 대신 [app/dashboard_job_enqueue_runtime.py](../app/dashboard_job_enqueue_runtime.py) 에서 관리한다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert automation 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 현재 `github_webhook.py` 는 비슷한 branch/track helper를 별도로 들고 있어, webhook path까지의 완전한 공통화는 아직 하지 않았다.
  - 이번 슬라이스는 enqueue helper 경계 분리까지이며, route grouping 자체나 runtime input/integration admin route 구조는 그대로다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_job_enqueue_runtime.py app/dashboard_memory_admin_runtime.py app/dashboard_issue_registration_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_enqueue_runtime.py tests/test_dashboard_issue_registration_runtime.py tests/test_dashboard_memory_admin_runtime.py tests/test_dashboard_app_registry_runtime.py` -> `17 passed`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py -k "issue_register or upsert_app"` -> `5 passed, 10 deselected`

### Durable self-check alert lifecycle baseline

- [app/durable_runtime_self_check.py](../app/durable_runtime_self_check.py) 에 persisted alert state를 붙였다.
  - self-check warning set 기준으로 `open` / `acknowledged` / `resolved` lifecycle 을 계산한다.
  - 최신 alert state는 [app/config.py](../app/config.py) 의 `durable_runtime_self_check_alert_file` 경로인 `data/durable_runtime_self_check_alert.json` 에 저장된다.
  - active alert acknowledge, clean run 이후 resolved 전이, stale/missing report 상황의 current payload 우선 merge 규칙까지 포함한다.
- [app/dashboard.py](../app/dashboard.py) 는 이제 self-check runtime builder에 alert file을 주입하고, `POST /api/admin/durable-runtime-self-check/alert/acknowledge` 경로를 제공한다.
- [app/self_check_main.py](../app/self_check_main.py) 도 같은 alert file을 사용하도록 맞췄다.
- [app/templates/index.html](../app/templates/index.html) 의 `Periodic Self-Check` 카드는 이제 alert state, acknowledge 버튼, acknowledged/resolved metadata, alert file 경로를 같이 보여준다.
- 관련 핵심 계약:
  - [tests/test_durable_runtime_self_check.py](../tests/test_durable_runtime_self_check.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `2261 -> 2285` lines 이다.
  - self-check baseline은 이제 report 파일과 alert 파일을 함께 유지하지만, 외부 notification/webhook 전송까지는 아직 하지 않는다.
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alert automation 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 현재 alert fingerprint는 warning code set 기준이라, 같은 code 조합 안에서 메시지 세부가 바뀌어도 같은 alert 로 간주한다.
  - dashboard acknowledge 는 operator note 입력 UI 없이 기본 `acted_by=dashboard` 로만 남긴다.
  - outbound notification, escalation, webhook 연계는 이번 슬라이스 범위 밖이다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/durable_runtime_self_check.py app/self_check_main.py app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_durable_runtime_self_check.py` -> `5 passed`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "durable_runtime_self_check"` -> `3 passed, 30 deselected`

### Dashboard assistant diagnosis runtime split

- [app/dashboard_assistant_diagnosis_runtime.py](../app/dashboard_assistant_diagnosis_runtime.py) 를 추가했다.
  - `build_agent_observability_context`
  - `derive_assistant_diagnosis_queries`
  - `build_assistant_diagnosis_runtime`
  - `run_assistant_diagnosis_loop`
  - `assistant_tool_docs_file`
  를 통해 assistant diagnosis trace/query/tool loop와 runtime context helper를 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 는 이제 [app/dashboard_assistant_runtime.py](../app/dashboard_assistant_runtime.py) builder에서 dashboard-local helper 대신 [app/dashboard_assistant_diagnosis_runtime.py](../app/dashboard_assistant_diagnosis_runtime.py) 의 direct method delegation을 사용한다.
  - assistant diagnosis loop
  - diagnosis query/context 조립
  - recent failed/running job observability context
- 목적은 `remaining runtime split / read-service long-tail` 에서 assistant read-heavy diagnosis helper를 먼저 빼 `dashboard.py` 를 계속 얇게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_assistant_diagnosis_runtime.py](../tests/test_dashboard_assistant_diagnosis_runtime.py)
  - [tests/test_dashboard_assistant_runtime.py](../tests/test_dashboard_assistant_runtime.py)
  - [tests/test_assistant_chat.py](../tests/test_assistant_chat.py)
  - [tests/test_assistant_log_analysis.py](../tests/test_assistant_log_analysis.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `2441 -> 2261` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `2261` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alerting 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 assistant diagnosis/helper boundary 분리까지이며, `_run_log_analyzer` / `_run_assistant_chat_provider` 같은 route monkeypatch compatibility 포인트는 [app/dashboard.py](../app/dashboard.py) 에 그대로 남겨뒀다.
  - 기존 prompt shape 와 `ASSISTANT_DIAGNOSIS_TRACE.json` 계약은 유지하고 내부 helper 위치만 이동했다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_assistant_diagnosis_runtime.py app/dashboard_assistant_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_assistant_diagnosis_runtime.py tests/test_dashboard_assistant_runtime.py` -> `6 passed`
  - `.venv/bin/python -m pytest -q tests/test_assistant_chat.py tests/test_assistant_log_analysis.py tests/test_jobs_dashboard_api.py -k "assistant_diagnosis or assistant/chat or log_analysis"` -> `6 passed, 36 deselected`

### Dashboard observability helper delegation cleanup

- [app/dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 에 아래 observability/read helper를 올렸다.
  - `read_job_runtime_recovery_trace`
  - `read_dashboard_jsonl`
  - `top_counter_items`
  - `safe_average`
  - `latest_non_empty`
- [app/dashboard.py](../app/dashboard.py) 는 이제 job detail/admin metrics builder에서 dashboard-local wrapper 대신 [app/dashboard_job_runtime.py](../app/dashboard_job_runtime.py) 의 direct method delegation을 사용한다.
  - `runtime_recovery_trace` reader
  - dead-letter / requeue / needs-human summary 조립
  - log summary / integration health facet 조립
  - admin metrics 용 diagnosis trace / JSONL / counter utility
- 목적은 `remaining runtime split / read-service long-tail` 에서 `job detail` 과 `admin metrics` 가 공유하던 observability helper 중복을 먼저 걷어 `dashboard.py` 를 계속 얇게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_job_runtime.py](../tests/test_dashboard_job_runtime.py)
  - [tests/test_dashboard_admin_metrics_runtime.py](../tests/test_dashboard_admin_metrics_runtime.py)
  - [tests/test_dashboard_job_detail_runtime.py](../tests/test_dashboard_job_detail_runtime.py)
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `2792 -> 2441` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `2441` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alerting 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 observability helper delegation cleanup 까지이며, job detail HTML shell 과 issue/app/assistant write helper 등 일부 route-local 조립은 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - 기존 API shape 와 recovery/integration payload 계약은 유지하고 내부 helper 위치만 이동했다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard.py app/dashboard_job_runtime.py app/dashboard_job_detail_runtime.py app/dashboard_admin_metrics_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_runtime.py tests/test_dashboard_admin_metrics_runtime.py tests/test_dashboard_job_detail_runtime.py` -> `13 passed`
  - `.venv/bin/python -m pytest -q tests/test_node_runs_api.py tests/test_jobs_dashboard_api.py -k "admin_metrics or assistant_diagnosis or runtime_recovery_trace or job_detail_api"` -> `22 passed, 34 deselected`

### Dashboard job artifact / log helper runtime split

- [app/dashboard_job_artifact_runtime.py](../app/dashboard_job_artifact_runtime.py) 를 추가했다.
  - `read_agent_md_files`
  - `read_stage_md_snapshots`
  - `resolve_channel_log_path`
  - `parse_log_events`
  - `build_focus_job_log_context`
  - `tail_text_lines`
  를 통해 log path resolution, log event parsing, agent/stage markdown snapshot, focus job log context helper 책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 는 이제 builder를 통해 아래 경계가 같은 runtime을 재사용한다.
  - [app/dashboard_job_detail_runtime.py](../app/dashboard_job_detail_runtime.py)
  - [app/dashboard_assistant_runtime.py](../app/dashboard_assistant_runtime.py)
  - [app/dashboard_job_runtime.py](../app/dashboard_job_runtime.py)
  - log file route와 assistant diagnosis helper
- 목적은 `remaining runtime split / read-service long-tail` 에서 detail/assistant/log route가 공유하던 read-heavy file/artifact helper를 먼저 빼 `dashboard.py` 를 계속 얇게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_job_artifact_runtime.py](../tests/test_dashboard_job_artifact_runtime.py)
  - [tests/test_log_route_security.py](../tests/test_log_route_security.py)
  - [tests/test_dashboard_job_detail_runtime.py](../tests/test_dashboard_job_detail_runtime.py)
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)
  - [tests/test_dashboard_assistant_runtime.py](../tests/test_dashboard_assistant_runtime.py)
  - [tests/test_assistant_chat.py](../tests/test_assistant_chat.py)
  - [tests/test_assistant_log_analysis.py](../tests/test_assistant_log_analysis.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `2991 -> 2792` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `2792` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alerting 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 shared file/artifact helper boundary 분리까지이며, job detail HTML shell 과 일부 read-side operator surface 는 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - 기존 legacy debug log path 호환성과 `parse_log_events` 의 최근 300개 event tail 계약은 유지하고 내부 조립 위치만 이동했다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard_job_artifact_runtime.py app/dashboard.py app/dashboard_job_detail_runtime.py app/dashboard_assistant_runtime.py app/dashboard_job_runtime.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_artifact_runtime.py tests/test_log_route_security.py tests/test_dashboard_job_detail_runtime.py tests/test_node_runs_api.py` -> `33 passed, 2 warnings`
  - `.venv/bin/python -m pytest -q tests/test_assistant_chat.py tests/test_assistant_log_analysis.py tests/test_dashboard_assistant_runtime.py` -> `14 passed`

### Dashboard jobs list / options read runtime split

- [app/dashboard_job_list_runtime.py](../app/dashboard_job_list_runtime.py) 를 추가했다.
  - `list_jobs_payload`
  - `get_job_options_payload`
  - `list_dashboard_jobs`
  를 통해 `jobs list / options` read path 조립과 filtering/pagination helper 책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 의 아래 경로는 이제 thin wrapper만 남긴다.
  - `GET /api/jobs`
  - `GET /api/jobs/options`
- 목적은 `remaining runtime split / read-service long-tail` 에서 반복 호출되는 list/filter/read 조각을 먼저 runtime으로 옮겨 `dashboard.py` 와 admin metrics 의 공통 read helper를 얇게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_job_list_runtime.py](../tests/test_dashboard_job_list_runtime.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_dashboard_admin_metrics_runtime.py](../tests/test_dashboard_admin_metrics_runtime.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `3379 -> 3207` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3207` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alerting 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 jobs list/options read 조립과 helper delegation 까지이며, job detail page HTML route 와 나머지 read-heavy operator surface 는 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - 기존 API shape 는 유지하고 내부 조립 위치만 이동했다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard_job_list_runtime.py app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_list_runtime.py tests/test_jobs_dashboard_api.py` -> `35 passed`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_admin_metrics_runtime.py` -> `1 passed`

### Dashboard job detail / node-runs read runtime split

- [app/dashboard_job_detail_runtime.py](../app/dashboard_job_detail_runtime.py) 를 추가했다.
  - `get_job_detail_payload`
  - `get_job_node_runs_payload`
  를 통해 `job detail / node-runs` read path 조립 책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 의 아래 경로는 이제 thin wrapper만 남긴다.
  - `GET /api/jobs/{job_id}`
  - `GET /api/jobs/{job_id}/node-runs`
- 목적은 `remaining runtime split / read-service long-tail` 중 가장 큰 read-heavy 블록을 먼저 줄여 `dashboard.py` 를 계속 얇게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_job_detail_runtime.py](../tests/test_dashboard_job_detail_runtime.py)
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)
- 현재 상태:
  - [app/dashboard.py](../app/dashboard.py) 는 `3425 -> 3379` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3379` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alerting 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 route 본문 분리까지이며, workflow resolution / resume helper 본체는 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - 기존 API shape 는 유지하고 내부 조립 위치만 이동했다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/dashboard_job_detail_runtime.py app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_job_detail_runtime.py tests/test_node_runs_api.py` -> `27 passed, 2 warnings`

### Phase 7-E4 secret rotation / reverse-proxy TLS runbook baseline

- [docs/REVERSE_PROXY_TLS_RUNBOOK.md](../docs/REVERSE_PROXY_TLS_RUNBOOK.md) 를 추가했다.
  - reverse proxy / TLS termination
  - 권장 `.env`
  - `nginx` 예시
  - smoke test / rollback 절차
  를 한 runbook 으로 정리했다.
- [app/security_governance_runtime.py](../app/security_governance_runtime.py) 는 이제 아래 operator-facing payload 를 같이 반환한다.
  - `operator_checklist`
  - `recommended_env`
  - `docs.tls_runbook`
- [app/templates/index.html](../app/templates/index.html) 의 `Security / TLS Governance` detail 은 이제 운영 체크리스트, 권장 ENV, runbook 경로를 함께 보여준다.
- [README.md](../README.md), [SECURITY.md](../SECURITY.md), [docs/DOCUMENT_MAP.md](../docs/DOCUMENT_MAP.md), [docs/PRODUCTION_READINESS_TRIAGE_PLAN.md](../docs/PRODUCTION_READINESS_TRIAGE_PLAN.md), [docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](../docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md), [docs/PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md](../docs/PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md) 를 현재 운영 절차 기준으로 갱신했다.
- 현재 상태:
  - secret rotation runbook 과 reverse proxy/TLS runbook 이 분리된 운영 source-of-truth 로 연결됐다.
  - `Security / TLS Governance` payload 는 단순 posture 경고에서 operator checklist + 권장 ENV + runbook pointer 까지 표면화한다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3425` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `remaining runtime split / read-service long-tail 정리`
  2. `durable backend / self-check alerting 경계 보강`
  3. `LICENSE / 정책 의사결정`
- 리스크 / 가정:
  - 이번 슬라이스는 운영 절차와 posture guidance 정례화까지이며, 실제 secret 교체/인증서 발급/프록시 반영은 여전히 운영 수행이 필요하다.
  - `operator_checklist` 는 현재 reverse proxy/LB 종료를 기본 운영 모델로 가정한다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/security_governance_runtime.py app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_security_governance_runtime.py tests/test_jobs_dashboard_api.py -k "security_governance"` -> `3 passed, 31 deselected`

### Phase 7-E3 periodic self-check baseline

- [app/durable_runtime_self_check.py](../app/durable_runtime_self_check.py) 를 추가했다.
  - patch status
  - latest patch run / updater status
  - post-update health
  - durable runtime hygiene
  - security governance
  를 하나의 persisted self-check report 로 합친다.
- [app/self_check_main.py](../app/self_check_main.py) 를 추가했다.
  - `systemd` timer 가 호출하는 one-shot self-check entrypoint 다.
- [app/dashboard.py](../app/dashboard.py) 와 [app/templates/index.html](../app/templates/index.html) 에 아래 operator surface 를 추가했다.
  - `GET /api/admin/durable-runtime-self-check`
  - `POST /api/admin/durable-runtime-self-check/run`
  - `Periodic Self-Check` admin 카드
- [systemd/agenthub-self-check.service](../systemd/agenthub-self-check.service), [systemd/agenthub-self-check.timer](../systemd/agenthub-self-check.timer) 를 추가했고, [scripts/install_systemd.sh](../scripts/install_systemd.sh) 는 timer enable + 초기 1회 실행까지 수행한다.
- [app/config.py](../app/config.py) 에 아래 경계를 추가했다.
  - `durable_runtime_self_check_report_file`
  - `AGENTHUB_SELF_CHECK_STALE_MINUTES` 기본값 `45`
- 현재 상태:
  - 최신 self-check report 는 `data/durable_runtime_self_check_report.json` 에 남는다.
  - 기본 timer 주기는 `15min` 이고 stale 기준은 `45min` 이다.
  - [app/dashboard.py](../app/dashboard.py) 는 `3368 -> 3425` lines 가 됐다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3425` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `실제 secret rotation / reverse-proxy TLS 운영 절차 정례화`
  2. `remaining runtime split / read-service long-tail 정리`
  3. `durable backend / self-check alerting 경계 보강`
- 리스크 / 가정:
  - self-check 는 observability baseline 이며 자동 복구/알림 전송까지는 아직 하지 않는다.
  - `PatchHealthRuntime` 은 `systemctl is-active` 와 로컬 `/healthz` 호출을 사용하므로 실제 운영 환경에서는 self-check service 가 systemd/journal 접근 권한을 가진다고 가정한다.
  - timer 미설치 환경에서도 dashboard 수동 실행은 가능하지만, stale 경고는 계속 남는다.
- 검증 결과:
  - `.venv/bin/python -m py_compile app/durable_runtime_self_check.py app/self_check_main.py app/dashboard.py` -> `ok`
  - `.venv/bin/python -m pytest -q tests/test_durable_runtime_self_check.py tests/test_jobs_dashboard_api.py -k "durable_runtime_self_check or durable_runtime_hygiene or security_governance or patch_updater or patch_run"` -> `13 passed, 22 deselected`
  - `.venv/bin/python -m pytest -q tests/test_setup_local_config_script.py tests/test_security_governance_runtime.py tests/test_durable_runtime_hygiene.py` -> `6 passed`
  - `.venv/bin/python -m pytest -q tests/test_main_https_enforcement.py` -> `2 passed`
  - `.venv/bin/python -m pytest -q tests/test_patch_health_runtime.py` -> `2 passed`

### Dashboard issue registration write/service split

- [app/dashboard_issue_registration_runtime.py](../app/dashboard_issue_registration_runtime.py) 를 추가했다.
  - `register_issue`
  책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 의 아래 경로는 이제 thin wrapper만 남긴다.
  - `POST /api/issues/register`
- 목적은 `dashboard write action/service` 잔여 중 마지막 큰 mutation 블록이던 issue registration 을 추출해, dashboard write/service 축소 범위를 현재 목표 수준에서 닫는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_issue_registration_runtime.py](../tests/test_dashboard_issue_registration_runtime.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
- 현재 상태:
  - `dashboard.py` 는 `3496 -> 3368` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3368` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `durable runtime periodic self-check`
  2. `실제 secret rotation / reverse-proxy TLS 운영 절차 정례화`
  3. `remaining runtime split / read-service long-tail 정리`
- 리스크 / 가정:
  - issue registration 은 builder 주입으로 기존 `gh issue create/edit`, label ensure, active-job dedupe, workflow selection 계약을 유지한다.
  - dashboard write/service 축소는 현재 목표 조각을 닫았지만, read-heavy block과 잡 상세 조합 로직은 여전히 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
- 검증 결과:
  - `.venv/bin/python -m pytest -q tests/test_dashboard_issue_registration_runtime.py` -> `3 passed`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py -k "issue_register"` -> `3 passed, 12 deselected`
  - `.venv/bin/python -m py_compile app/dashboard_issue_registration_runtime.py app/dashboard.py` -> `ok`

### Dashboard memory admin write/service split

- [app/dashboard_memory_admin_runtime.py](../app/dashboard_memory_admin_runtime.py) 를 추가했다.
  - `search_entries`
  - `list_backlog_candidates`
  - `apply_backlog_action`
  - `get_memory_detail`
  - `override_memory`
  책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 의 아래 경로는 이제 thin wrapper만 남긴다.
  - `GET /api/admin/memory/search`
  - `GET /api/admin/memory/backlog`
  - `POST /api/admin/memory/backlog/{candidate_id}/action`
  - `GET /api/admin/memory/{memory_id}`
  - `POST /api/admin/memory/{memory_id}/override`
- 목적은 `dashboard write action/service` 잔여 중 `memory admin` 블록을 먼저 줄여, 다음 `issue registration` 추출 슬라이스를 받기 쉽게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_memory_admin_runtime.py](../tests/test_dashboard_memory_admin_runtime.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - `dashboard.py` 는 `3633 -> 3496` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3496` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `durable runtime periodic self-check`
  2. `실제 secret rotation / reverse-proxy TLS 운영 절차 정례화`
  3. `remaining runtime split / read-service long-tail 정리`
- 리스크 / 가정:
  - 당시에는 `issue register` 액션이 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있었다.
  - backlog `queue` 액션은 기존 `_queue_followup_job_from_backlog_candidate()` 브리지를 builder 주입으로 그대로 재사용한다.
- 검증 결과:
  - `.venv/bin/python -m pytest -q tests/test_dashboard_memory_admin_runtime.py` -> `6 passed`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "admin_memory_search_api or admin_memory_backlog_api or admin_memory_backlog_action_api or conv_pytest_file_pattern"` -> `3 passed, 27 deselected`
  - `.venv/bin/python -m py_compile app/dashboard_memory_admin_runtime.py app/dashboard.py` -> `ok`

### Dashboard assistant write/service split

- [app/dashboard_assistant_runtime.py](../app/dashboard_assistant_runtime.py) 를 추가했다.
  - `chat`
  - `log_analysis`
  책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 의 아래 경로는 이제 thin wrapper만 남긴다.
  - `POST /api/assistant/chat`
  - `POST /api/assistant/log-analysis`
  - `POST /api/assistant/codex-chat` 는 기존 호환 경로를 유지한 채 위 runtime을 재사용한다.
- 목적은 `dashboard write action/service` 잔여 중 `assistant chat / log-analysis` 블록을 먼저 줄여, 다음 `issue registration / memory admin` 추출 슬라이스를 받기 쉽게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_assistant_runtime.py](../tests/test_dashboard_assistant_runtime.py)
  - [tests/test_assistant_chat.py](../tests/test_assistant_chat.py)
  - [tests/test_assistant_log_analysis.py](../tests/test_assistant_log_analysis.py)
- 현재 상태:
  - `dashboard.py` 는 `3736 -> 3633` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3633` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `dashboard write action/service` 잔여의 다음 조각(`issue registration`)
  2. `durable runtime periodic self-check`
  3. `실제 secret rotation / reverse-proxy TLS 운영 절차 정례화`
- 리스크 / 가정:
  - 이번 턴은 assistant 블록만 추출했고 당시에는 `issue register`, `memory search/detail/override/backlog` 계열 action이 [app/dashboard.py](../app/dashboard.py) 에 남아 있었다.
  - 기존 monkeypatch 포인트(`_run_assistant_chat_provider`, `_run_log_analyzer`, `_run_assistant_diagnosis_loop`)는 builder 주입으로 유지했다.
- 검증 결과:
  - `.venv/bin/python -m pytest -q tests/test_dashboard_assistant_runtime.py tests/test_assistant_chat.py tests/test_assistant_log_analysis.py` -> `14 passed`
  - `.venv/bin/python -m py_compile app/dashboard_assistant_runtime.py app/dashboard.py` -> `ok`

### Dashboard settings write/service split

- [app/dashboard_settings_runtime.py](../app/dashboard_settings_runtime.py) 를 추가했다.
  - `workflow_schema`
  - `list_workflows`
  - `validate_workflow`
  - `save_workflow`
  - `set_default_workflow`
  - `get_feature_flags`
  - `save_feature_flags`
  - `get_agent_config`
  - `update_agent_config`
  - `get_agent_cli_status`
  - `get_agent_model_status`
  책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 의 아래 경로는 이제 thin wrapper만 남긴다.
  - `GET /api/workflows/schema`
  - `GET /api/workflows`
  - `POST /api/workflows/validate`
  - `POST /api/workflows`
  - `POST /api/workflows/default`
  - `GET /api/feature-flags`
  - `POST /api/feature-flags`
  - `GET /api/agents/config`
  - `POST /api/agents/config`
  - `GET /api/agents/check`
  - `GET /api/agents/models`
- 목적은 `dashboard write action/service` 잔여 중 `workflow / feature flag / agent config` 묶음을 먼저 줄여, 다음 `assistant / issue registration / memory admin` 추출 슬라이스를 받기 쉽게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_settings_runtime.py](../tests/test_dashboard_settings_runtime.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - `dashboard.py` 는 `3758 -> 3736` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3736` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `dashboard write action/service` 잔여의 다음 조각(`assistant / issue registration / memory admin`)
  2. `durable runtime periodic self-check`
  3. `실제 secret rotation / reverse-proxy TLS 운영 절차 정례화`
- 리스크 / 가정:
  - 이번 턴은 settings 블록만 추출했고 `assistant chat/log-analysis`, `issue register`, `memory override` 계열 action은 아직 [app/dashboard.py](../app/dashboard.py) 에 남아 있다.
  - workflow validation 실패 응답은 기존과 동일하게 `HTTP 400 + {message, errors}` shape를 유지한다.
- 검증 결과:
  - `.venv/bin/python -m pytest -q tests/test_dashboard_settings_runtime.py tests/test_workflow_settings_api.py` -> `21 passed`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "agent_cli_check_api_returns_git_and_gh or agent_models_api_reports_dangerous_codex_templates"` -> `2 passed, 28 deselected`

### Dashboard app registry write/service split

- [app/dashboard_app_registry_runtime.py](../app/dashboard_app_registry_runtime.py) 를 추가했다.
  - `list_apps`
  - `upsert_app`
  - `delete_app`
  - `map_app_workflow`
  책임을 라우터 밖으로 이동했다.
- [app/dashboard.py](../app/dashboard.py) 의 아래 경로는 이제 thin wrapper만 남긴다.
  - `GET /api/apps`
  - `POST /api/apps`
  - `DELETE /api/apps/{app_code}`
  - `POST /api/apps/{app_code}/workflow`
- 목적은 `dashboard write action/service` 잔여 중 `apps` 관리 블록을 먼저 줄여, 다음 `workflow / feature flag / agent config` 추출 슬라이스를 받기 쉽게 만드는 것이다.
- 관련 핵심 계약:
  - [tests/test_dashboard_app_registry_runtime.py](../tests/test_dashboard_app_registry_runtime.py)
  - [tests/test_workflow_settings_api.py](../tests/test_workflow_settings_api.py)
- 현재 상태:
  - `dashboard.py` 는 `3875 -> 3758` lines 로 줄었다.
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3758` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `dashboard write action/service` 잔여의 다음 조각(`workflow / feature flag / agent config`)
  2. `durable runtime periodic self-check`
  3. `실제 secret rotation / reverse-proxy TLS 운영 절차 정례화`
- 리스크 / 가정:
  - 이번 턴은 `apps` 관리만 추출했고 workflow editor/feature flag/agent config write path는 아직 `dashboard.py`에 남아 있다.
  - route contract는 유지했고, side effect(`gh label create`)도 기존 helper를 그대로 주입받는다.
- 검증 결과:
  - `.venv/bin/python -m pytest -q tests/test_dashboard_app_registry_runtime.py` -> `5 passed`
  - `.venv/bin/python -m pytest -q tests/test_workflow_settings_api.py` -> `15 passed`

### Phase 7-E2 security / TLS governance baseline

- [app/security_governance_runtime.py](../app/security_governance_runtime.py) 를 추가했다.
  - `AGENTHUB_PUBLIC_BASE_URL`
  - `AGENTHUB_ENFORCE_HTTPS`
  - `AGENTHUB_TRUST_X_FORWARDED_PROTO`
  - CORS allow-list / wildcard
  - webhook secret 길이 / test-like 값 여부
  를 한 번에 점검한다.
- [app/main.py](../app/main.py) 는 이제 `AGENTHUB_ENFORCE_HTTPS=true` 일 때 `/healthz`를 제외한 HTTP 요청을 `426 https_required`로 거부할 수 있다.
  - `AGENTHUB_TRUST_X_FORWARDED_PROTO=true` 면 `X-Forwarded-Proto` 기준으로 HTTPS를 판정한다.
- [app/dashboard.py](../app/dashboard.py) 에 `GET /api/admin/security-governance` 를 추가했다.
- [app/templates/index.html](../app/templates/index.html) 에 `Security / TLS Governance` 카드와 `거버넌스 점검` 버튼을 추가했다.
- 운영 설정 예시와 위생 검사도 같이 맞췄다.
  - [app/config.py](../app/config.py)
  - [.env.example](../.env.example)
  - [scripts/setup_local_config.sh](../scripts/setup_local_config.sh)
  - [scripts/check_repo_hygiene.py](../scripts/check_repo_hygiene.py)
- 관련 핵심 계약:
  - [tests/test_security_governance_runtime.py](../tests/test_security_governance_runtime.py)
  - [tests/test_main_https_enforcement.py](../tests/test_main_https_enforcement.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [tests/test_repo_hygiene.py](../tests/test_repo_hygiene.py)
  - [tests/test_setup_local_config_script.py](../tests/test_setup_local_config_script.py)
- 현재 상태:
  - `7-E2 security / TLS governance` baseline implemented
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3875` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `dashboard write action/service` 잔여 축소
  2. `durable runtime periodic self-check`
  3. `실제 secret rotation / reverse-proxy TLS 운영 절차 정례화`
- 리스크 / 가정:
  - 현재 HTTPS 강제는 app-level baseline이다. 실제 TLS termination/certificate 운영은 reverse proxy/LB 절차가 따로 필요하다.
  - `/healthz` 는 내부 점검 경로를 위해 HTTP 예외로 유지한다.
  - secret rotation 자체는 여전히 운영 작업이며, 이번 턴은 posture surface + 경계 강화까지만 닫았다.
- 검증 결과:
  - `.venv/bin/python -m pytest -q tests/test_security_governance_runtime.py` -> `2 passed`
  - `.venv/bin/python -m pytest -q tests/test_main_https_enforcement.py` -> `2 passed`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py -k "security_governance or durable_runtime_hygiene or patch_status or patch_run or patch_updater"` -> `9 passed, 21 deselected`
  - `.venv/bin/python -m pytest -q tests/test_repo_hygiene.py` -> `2 passed`
  - `.venv/bin/python -m pytest -q tests/test_setup_local_config_script.py` -> `2 passed`

### Phase 7-E1 durable runtime / workspace hygiene baseline

- [app/durable_runtime_hygiene.py](../app/durable_runtime_hygiene.py) 를 추가했다.
  - 오래된 `patch backup`
  - `invalid workspace backup`
  - queue `orphan / duplicate / stale`
  - active patch run이 없는 `stale patch lock`
  을 한 번에 감사한다.
- cleanup은 live workspace를 자동 삭제하지 않고 안전한 대상만 정리한다.
  - retention 지난 terminal/orphan patch backup
  - invalid workspace backup
  - queue leftover
  - stale patch lock
- [app/dashboard.py](../app/dashboard.py) 에 아래 API를 추가했다.
  - `GET /api/admin/durable-runtime-hygiene`
  - `POST /api/admin/durable-runtime-hygiene/cleanup`
- [app/templates/index.html](../app/templates/index.html) 에 `Durable Runtime Hygiene` 카드와 `위생 점검 / 안전 정리 실행` 버튼을 추가했다.
- 최신 cleanup report는 `data/durable_runtime_hygiene_report.json`에 남고, 보존 기준은 [app/config.py](../app/config.py) 의 `AGENTHUB_DURABLE_RETENTION_DAYS`(기본 `7`)다.
- 관련 핵심 계약:
  - [tests/test_durable_runtime_hygiene.py](../tests/test_durable_runtime_hygiene.py)
  - [tests/test_dashboard_patch_runtime.py](../tests/test_dashboard_patch_runtime.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 현재 상태:
  - `7-E1 durable runtime / workspace hygiene` baseline implemented
  - 최신 큰 파일 상태:
    - [app/dashboard.py](../app/dashboard.py): `3856` lines
    - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 다음 우선순위 1~3:
  1. `enterprise 운영 거버넌스 / secret / TLS`
  2. `dashboard write action/service` 잔여 축소
  3. `durable runtime periodic self-check`
- 리스크 / 가정:
  - 현재 cleanup은 `live workspace`를 삭제하지 않는다. unmanaged workspace는 operator review 대상으로만 남긴다.
  - patch backup cleanup은 `done/restored` 또는 orphan backup만 retention policy로 정리한다. `failed/rolled_back/restore_failed` 계열 backup은 보호한다.
  - periodic self-check/systemd timer는 아직 없다. 현재는 operator-triggered baseline이다.
- 검증 결과:
  - `.venv/bin/python -m pytest -q tests/test_durable_runtime_hygiene.py` -> `2 passed`
  - `.venv/bin/python -m pytest -q tests/test_dashboard_patch_runtime.py` -> `9 passed`
  - `.venv/bin/python -m pytest -q tests/test_jobs_dashboard_api.py` -> `29 passed`

### Phase 7-D2 restore action / backup verification

- [app/patch_backup_runtime.py](../app/patch_backup_runtime.py) 는 이제 `verify_backup_manifest()`를 제공하고, `restore_backup()`도 복원 전에 manifest와 실제 백업 파일을 먼저 검증한다.
- [app/dashboard_patch_runtime.py](../app/dashboard_patch_runtime.py) 는 failed / rollback_failed / rolled_back / restore_failed patch run에 대해 `restore_requested`를 기록할 수 있다.
  - operator note
  - restore source status
  - verified backup manifest
  - next action
  을 patch run details에 남긴다.
- [app/patch_updater_runtime.py](../app/patch_updater_runtime.py) 는 이제
  - `restore_requested`
  - `restoring`
  - `restore_verifying`
  상태를 처리한다.
  - backup manifest verification 실패 시 `restore_failed + manual_restore_required`
  - 복원 후 서비스 재기동과 post-restore health check까지 수행
  - 성공 시 `restored`
  - 실패 시 `restore_failed + manual_restore_check_required`
  로 종료한다.
- [app/dashboard.py](../app/dashboard.py), [app/templates/index.html](../app/templates/index.html) 에 `복원 요청` API/버튼과 복원 검증/복원 결과 표면이 추가됐다.
- 최신 기준 큰 파일 상태:
  - [app/dashboard.py](../app/dashboard.py): `3821` lines
  - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 최신 전체 검증:
  - `465 passed, 12 warnings`
- 관련 핵심 계약:
  - [tests/test_patch_backup_runtime.py](../tests/test_patch_backup_runtime.py)
  - [tests/test_dashboard_patch_runtime.py](../tests/test_dashboard_patch_runtime.py)
  - [tests/test_patch_updater_runtime.py](../tests/test_patch_updater_runtime.py)
  - [tests/test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 다음 단계:
  - `7-E1 durable runtime / workspace hygiene`
  - `dashboard write action/service` 잔여 축소
  - `enterprise 운영 거버넌스 / secret / TLS`

### Optional helper failure noise reduction

- [app/templates/job_detail.html](../app/templates/job_detail.html) 의 사용자 로그 역할 카드 집계가 이제 `TECH_WRITER`, `PR_SUMMARY`, `COMMIT_SUMMARY`, `ESCALATION` 같은 보조 helper의 non-zero 종료를 `실패`가 아니라 `주의`로 센다.
- 상단 역할 카드와 실행 흐름 요약 배지도 보조 helper 실패는 빨간 `실패` 대신 amber 성격의 `주의(exit N)`로 보여준다.
- 목적은 기술 문서 작성가/PR 요약기 같은 보조 route가 자주 fallback되더라도 operator가 이를 본작업 실패로 오인하지 않게 하는 것이다.
- 관련 회귀:
  - [tests/test_node_runs_api.py](../tests/test_node_runs_api.py)
  - 최신 전체 검증: `456 passed, 12 warnings`

### Phase 7-C2 rollback baseline

- [app/patch_rollback_runtime.py](../app/patch_rollback_runtime.py) 를 추가했다.
  - `.git` 저장소 여부
  - dirty working tree 여부
  - target commit 존재 여부
  를 확인한 뒤 `git checkout -B <branch> <target_commit>` 기준으로 롤백한다.
- [app/dashboard_patch_runtime.py](../app/dashboard_patch_runtime.py) 는 failed patch run에 대해 `rollback_requested`를 기록할 수 있다.
  - 운영자 note
  - rollback target commit
  - 다음 액션
  을 patch run details에 남긴다.
- [app/patch_updater_runtime.py](../app/patch_updater_runtime.py) 는 이제
  - `rollback_requested`
  - `rolling_back`
  - `rollback_verifying`
  상태를 처리한다.
  - rollback 후 서비스 재기동을 수행한다.
  - health check가 통과하면 `rolled_back`
  - 실패하면 `rollback_failed + manual_rollback_check_required`
  로 종료한다.
- [app/dashboard.py](../app/dashboard.py), [app/templates/index.html](../app/templates/index.html) 에 `실패 시 롤백` API/버튼이 추가됐다.
- 최신 기준 큰 파일 상태:
  - [app/dashboard.py](../app/dashboard.py): `3775` lines
  - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 최신 전체 검증:
  - `456 passed, 12 warnings`
- 관련 핵심 계약:
  - [test_patch_rollback_runtime.py](../tests/test_patch_rollback_runtime.py)
  - [test_patch_updater_runtime.py](../tests/test_patch_updater_runtime.py)
  - [test_dashboard_patch_runtime.py](../tests/test_dashboard_patch_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 다음 단계:
  - `7-D1 backup / restore + patch coupling`
  - `durable runtime / workspace hygiene`
  - `dashboard write action/service` 잔여 축소

### Phase 7-C1 post-update health check

- [app/patch_health_runtime.py](../app/patch_health_runtime.py) 를 추가했다.
  - API `/healthz`
  - worker service active 상태
  - queue/store 접근
  - patch lock 해제 여부
  - updater status payload
  를 패치 직후 점검한다.
- [app/patch_updater_runtime.py](../app/patch_updater_runtime.py) 는 이제 `draining -> verifying -> done/failed`를 수행한다.
  - 재기동 직후 patch run은 `verifying` 단계로 진입한다.
  - health check가 통과하면 `done`
  - 실패하면 `failed + manual_post_update_check_required`
  로 종료된다.
- [app/updater_main.py](../app/updater_main.py) 는 updater service에 `PatchHealthRuntime`을 같이 연결한다.
- 최신 기준 큰 파일 상태:
  - [app/dashboard.py](../app/dashboard.py): `3775` lines
  - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 최신 전체 검증:
  - `456 passed, 12 warnings`
- 관련 핵심 계약:
  - [test_patch_health_runtime.py](../tests/test_patch_health_runtime.py)
  - [test_patch_updater_runtime.py](../tests/test_patch_updater_runtime.py)
  - [test_patch_service_runtime.py](../tests/test_patch_service_runtime.py)
- 다음 단계:
  - `7-D1 backup / restore + patch coupling`
  - `durable runtime / workspace hygiene`
  - `dashboard write action/service` 잔여 축소

### Phase 7-B2 service drain / stop / restart

- [app/patch_service_runtime.py](../app/patch_service_runtime.py) 를 추가했다.
  - patch lock 상태 파일을 관리한다.
  - patch 진행 중 새 작업 수락 차단 경계를 제공한다.
  - `worker stop -> api restart -> worker restart` 순서의 서비스 재기동 baseline을 수행한다.
- [app/patch_updater_runtime.py](../app/patch_updater_runtime.py) 는 이제 `waiting_updater -> draining -> restarting` 상태 전이를 실제로 수행한다.
  - dirty working tree / ahead-of-upstream는 시작 전에 `failed`로 전이한다.
  - active job이 남아 있으면 drain 상태를 유지한다.
  - active job이 비면 service restart를 수행하고 patch lock을 해제한다.
  - 다음 단계는 `7-C1 post-update health check`였다.
- [app/dashboard_job_action_runtime.py](../app/dashboard_job_action_runtime.py) 의 주요 requeue action은 patch lock 활성 시 `409`로 막힌다.
- [app/dashboard.py](../app/dashboard.py) 의 dashboard issue register도 patch lock 활성 시 새 작업 등록을 막는다.
- [app/github_webhook.py](../app/github_webhook.py) 는 patch lock 활성 시 webhook enqueue를 `accepted=false, reason=patch_in_progress`로 돌려준다.
- [app/updater_main.py](../app/updater_main.py) 는 updater service에 `PatchServiceRuntime`을 같이 연결한다.
- 최신 기준 큰 파일 상태:
  - [app/dashboard.py](../app/dashboard.py): `3753` lines
  - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
- 최신 전체 검증:
  - `444 passed, 11 warnings`
- 관련 핵심 계약:
  - [test_patch_service_runtime.py](../tests/test_patch_service_runtime.py)
  - [test_patch_updater_runtime.py](../tests/test_patch_updater_runtime.py)
  - [test_dashboard_patch_runtime.py](../tests/test_dashboard_patch_runtime.py)
  - [test_dashboard_job_action_runtime.py](../tests/test_dashboard_job_action_runtime.py)
  - [test_webhook_enqueue.py](../tests/test_webhook_enqueue.py)
- 다음 단계:
  - `7-C1 post-update health check`
  - `7-C2 rollback baseline`
  - `7-D1 backup / restore + patch coupling`

### CLI 연결 확인에 git / gh 추가

- [app/agent_config_runtime.py](../app/agent_config_runtime.py) 의 `collect_agent_cli_status()`가 이제 `gemini`, `codex`뿐 아니라 `git`, `gh`도 같이 점검한다.
- [app/templates/index.html](../app/templates/index.html) 의 `CLI 연결 확인` 결과 박스도 `GEMINI / CODEX / GIT / GH` 순서로 표시한다.
- 관련 회귀:
  - [test_agent_cli_runtime.py](../tests/test_agent_cli_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 최신 검증:
  - 타깃 회귀 `29 passed`
  - 전체 회귀 `435 passed, 10 warnings`

### Ultra-long test safety profile

- 초장기/ultra 시험용 로컬 `.env` 안전 프로필 문서를 추가했다.
  - [ULTRA_LONG_TEST_SAFETY_PROFILE.md](./ULTRA_LONG_TEST_SAFETY_PROFILE.md)
- 현재 로컬 `.env`는 더 이상 `echo skip tests`를 쓰지 않고 아래 실테스트 명령을 사용한다.
  - `AGENTHUB_TEST_COMMAND="bash scripts/run_agenthub_tests.sh auto"`
  - `AGENTHUB_TEST_COMMAND_SECONDARY="bash scripts/run_agenthub_tests.sh auto"`
  - `AGENTHUB_TEST_COMMAND_IMPLEMENT="bash scripts/run_agenthub_tests.sh implement"`
  - `AGENTHUB_TEST_COMMAND_FIX="bash scripts/run_agenthub_tests.sh fix"`
  - `AGENTHUB_WORKER_STALE_RUNNING_SECONDS=7200`
  - `AGENTHUB_TEST_COMMAND_TIMEOUT_SECONDS=3600`
- 이 값은 저장소 기본값이 아니라 `로컬 운영 프로필`이다.
  - `.env.example`나 quickstart 기본값을 직접 바꾸지는 않는다.
- README와 문서 맵에도 초장기 시험용 프로필 링크를 연결했다.
  - [README.md](../README.md)
  - [DOCUMENT_MAP.md](./DOCUMENT_MAP.md)
- 실제 검증:
  - `bash scripts/run_agenthub_tests.sh auto`
  - 결과: `433 passed, 10 warnings`
- 다음 사용 순서:
  - 로컬 `.env`를 이 프로필로 유지
  - 초장기 잡 enqueue 전에 `auto` 테스트를 한 번 재확인
  - helper login/quota 경고는 본작업 실패와 분리해서 판정

### Phase 7-B1 separate updater service

- [app/patch_updater_runtime.py](../app/patch_updater_runtime.py) 를 추가했다.
  - updater heartbeat/status를 `data/patch_updater_status.json`에 기록한다.
  - 최신 `waiting_updater` patch run을 감지하면 claim 정보를 남긴다.
- [app/updater_main.py](../app/updater_main.py) 를 추가했다.
  - 별도 updater loop entrypoint baseline이다.
- [app/dashboard.py](../app/dashboard.py) 에 아래 API를 추가했다.
  - `GET /api/admin/patch-updater-status`
- [app/templates/index.html](../app/templates/index.html) 에 `Updater 서비스` 카드를 추가했다.
  - 상태
  - 서비스 이름
  - 활성 patch run
  - 마지막 heartbeat
  - 다음 액션
- [systemd/agenthub-updater.service](../systemd/agenthub-updater.service) 를 추가했고, [scripts/install_systemd.sh](../scripts/install_systemd.sh) 는 updater service도 설치/기동한다.
- 이번 슬라이스는 `claim + heartbeat + operator surface`까지다.
  - 실제 `service drain / stop / restart`는 아직 하지 않는다.
  - patch run의 `next_action`은 `service_drain_restart_not_implemented`로 남는다.
- 현재 기준 큰 파일 상태:
  - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
  - [app/dashboard.py](../app/dashboard.py): `3737` lines
- 최신 전체 검증:
  - `433 passed, 10 warnings`
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_patch_updater_runtime.py](../tests/test_patch_updater_runtime.py)
  - [test_patch_control_runtime.py](../tests/test_patch_control_runtime.py)
  - [test_dashboard_patch_runtime.py](../tests/test_dashboard_patch_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 다음 단계:
  - `7-B2 service drain / stop / restart`
  - `7-C1 post-update health check`

### Goal closure priority reset

- `부분 가능` 상태가 남아 있는 항목들을 목표 기준으로 다시 정리했다.
- 새 기준 문서:
  - [GOAL_CLOSURE_PRIORITY_RESET.md](./GOAL_CLOSURE_PRIORITY_RESET.md)
- 핵심 필수는 아래로 정리했다.
  - `self-learning loop strong`
  - `AI fallback strong`
  - `integration/operator control strong`
  - `mobile emulator/E2E strong`
  - `patch/update/durable runtime baseline`
- 후순위로 정리한 항목:
  - `한/영 UI 완전 다국어`
  - `그래프형 workflow 시각화`
- 즉 현재 기준 다음 우선순위는 `remaining runtime split` 자체보다 `Phase 7 updater/durable runtime + AI fallback strong + self-growing 장기 증명`이다.

### AI role execution policy alignment

- 역할 경계 기준 문서 [AI_ROLE_EXECUTION_POLICY.md](./AI_ROLE_EXECUTION_POLICY.md) 를 추가했다.
- 기본 정책은 아래로 정리했다.
  - `Gemini`: 계획/리뷰/테스트 해석/commit·PR·escalation 요약
  - `Codex`: 구현/리팩터/문서 실제 작성
  - `bash`: pytest / npm test / e2e / emulator 실행
- [config/ai_role_routing.json](../config/ai_role_routing.json), [app/ai_role_routing.py](../app/ai_role_routing.py) 기준 기본 route를 재정렬했다.
  - `documentation -> tech-writer (Codex)`
  - `commit_summary -> summary-reviewer (Gemini)`
  - `pr_summary -> summary-reviewer (Gemini)`
  - `escalation -> summary-reviewer (Gemini)`
  - `test_reviewer -> test-reviewer (Gemini baseline route)`
- [config/roles.json](../config/roles.json), [app/dashboard_roles_runtime.py](../app/dashboard_roles_runtime.py) 에 `summary-reviewer`, `test-reviewer` 기본 role을 추가했다.
- [config/ai_commands.json](../config/ai_commands.json), [config/ai_commands.example.json](../config/ai_commands.example.json) 에서
  - `commit_summary`
  - `pr_summary`
  - `escalation`
  을 Gemini 명령으로 옮겼다.
- [app/summary_runtime.py](../app/summary_runtime.py) 는 commit summary를 이제 Gemini route 먼저 시도하고, 실패 시 Codex helper fallback을 사용한다.
- commit summary route actor는 `TECH_WRITER` 대신 `COMMIT_SUMMARY`로 남도록 바꿨다.
- 로그 표시도 같이 맞췄다.
  - [app/log_signal_utils.py](../app/log_signal_utils.py)
  - [app/templates/job_detail.html](../app/templates/job_detail.html)
  - `COMMIT_SUMMARY`, `PR_SUMMARY`, `ESCALATION` 는 optional Gemini helper로 분류된다.
- 관련 회귀:
  - [test_ai_role_routing.py](../tests/test_ai_role_routing.py)
  - [test_summary_runtime.py](../tests/test_summary_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
- 다음 단계:
  - `test_reviewer` 전용 artifact / stage 연결
  - CLI health card와 route/provider 상태 surface

### Phase 7-A2 patch run state / progress

- [app/models.py](../app/models.py) 에 [PatchRunRecord](../app/models.py) 를 추가했다.
- [app/store.py](../app/store.py) 는 이제 patch run state를 JSON/SQLite 둘 다 저장/조회할 수 있다.
  - `upsert_patch_run()`
  - `get_patch_run()`
  - `list_patch_runs()`
- [app/dashboard_patch_runtime.py](../app/dashboard_patch_runtime.py) 를 추가했다.
  - patch run baseline step:
    - `approval_recorded`
    - `waiting_updater`
    - `drain_services`
    - `update_code`
    - `restart_services`
    - `verify_health`
  - 이번 슬라이스는 실제 updater 실행이 아니라 `waiting_updater`까지를 기록한다.
- [app/dashboard.py](../app/dashboard.py) 에 아래 API를 추가했다.
  - `GET /api/admin/patch-runs/latest`
  - `POST /api/admin/patch-runs`
- [app/templates/index.html](../app/templates/index.html) 운영 입력/상태 섹션에 `패치 실행 진행률` 카드를 추가했다.
  - 상태
  - 현재 단계
  - 진행률
  - 요청 시각
  - 운영자 메모
  - 현재 patch 기준 / 다음 액션
- 현재 baseline의 `next_action`은 `separate_updater_service_required`다.
- 이번 슬라이스로 `패치 상태 감지`와 `패치 실행 진행률 surface`가 분리됐다.
- 다음 단계는 `7-B2 service drain / stop / restart`다.
- 현재 기준 큰 파일 상태:
  - [app/orchestrator.py](../app/orchestrator.py): `2605` lines
  - [app/dashboard.py](../app/dashboard.py): `3737` lines
- 최신 전체 검증:
  - `430 passed, 10 warnings`
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_patch_control_runtime.py](../tests/test_patch_control_runtime.py)
  - [test_dashboard_patch_runtime.py](../tests/test_dashboard_patch_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)
  - [test_store_and_queue.py](../tests/test_store_and_queue.py)

### Phase 7-A1 patch status detection baseline

- [app/patch_control_runtime.py](../app/patch_control_runtime.py) 를 추가했다.
- admin 화면에서 현재 저장소의 patch/update 가능 여부를 읽는 baseline을 넣었다.
  - 현재 branch
  - 현재 commit / subject
  - upstream ref
  - behind / ahead
  - dirty working tree
  - update available 여부
- [app/dashboard.py](../app/dashboard.py) 에 아래 API를 추가했다.
  - `GET /api/admin/patch-status`
  - `refresh=1` 이면 `git fetch --quiet origin` 후 다시 계산한다.
- [app/templates/index.html](../app/templates/index.html) 운영 입력/상태 섹션에 `패치 상태` 카드를 추가했다.
  - 상태
  - branch / upstream
  - behind / ahead
  - working tree dirty 여부
  - operator-friendly 메시지
- 이번 슬라이스는 `감지`까지만 닫았다.
  - 서비스 중지/업데이트/재기동은 아직 하지 않는다.
- 관련 핵심 계약은 아래 테스트로 고정했다.
  - [test_patch_control_runtime.py](../tests/test_patch_control_runtime.py)
  - [test_jobs_dashboard_api.py](../tests/test_jobs_dashboard_api.py)

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

1. `7-C2 rollback baseline`
   - post-update health 실패 시 직전 commit 또는 지정 target으로 되돌리는 최소 경로

2. `7-D1 backup / restore + patch coupling`
   - patch 전후 백업/복구 절차를 상태 기계에 연결

3. `durable runtime / workspace hygiene`
   - patch/update 흐름과 장기 운영 cleanup 정책을 연결

4. `enterprise 운영 계층 보강`
   - usage trail, approval 경계, provider containment를 운영 surface로 더 조밀하게 엮는 단계

## 4. 주의할 점

- `LICENSE`는 아직 미정이다. 기술 패치로 임의 추가하지 않는다.
- 실제 운영 시크릿 로테이션과 Git 히스토리 정리는 아직 수행되지 않았다.
- `config/ai_commands.json`은 로컬 런타임 파일이다. 예시 파일과 혼동하지 않는다.
- 레거시 `Claude/Copilot` 이름은 일부 호환 alias로 남아 있다. 실제 기본 실행 경로는 `Gemini + Codex` 기준이다.
- feature 수만 늘리는 건 지금 해결책이 아니다. 구조 리스크와 운영 신뢰성부터 낮춰야 한다.
- 모바일 앱 검증 artifact는 baseline까지만 자동화되어 있고, safe-area/keyboard/offline의 실제 판정은 아직 수동 확인 항목으로 남아 있다.

## 5. 검증 결과

- patch health/drain 관련 타깃 회귀: `39 passed, 8 warnings`
- 최신 전체 회귀: `448 passed, 11 warnings`
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
  - [app/dashboard.py](../app/dashboard.py): `3754`
  - [app/orchestrator.py](../app/orchestrator.py): `2605`
- 최신 전체 회귀:
  - `448 passed, 11 warnings`
- 현재 다음 우선순위:
  1. `7-C2 rollback baseline`
  2. `7-D1 backup / restore + patch coupling`
  3. `durable runtime / workspace hygiene`
  4. `enterprise 운영 계층 보강`

## 7. 다음 세션 시작 순서

1. [README.md](../README.md)
2. [DOCUMENT_MAP.md](./DOCUMENT_MAP.md)
3. [AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](./AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md)
4. [CURRENT_STATE_GAP_REPORT.md](./CURRENT_STATE_GAP_REPORT.md)
5. 이 문서
6. 관련 대상 파일과 테스트
