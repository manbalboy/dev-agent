# Phase 5 Continuous Job Operations Expansion Plan

## 1. North Star
- Phase 5의 목표는 이 시스템을 `똑똑한 자동 실행기`에서 `24시간 운영 가능한 개발 동료`로 올리는 것이다.
- Phase 4가 tool/vector/graph를 통해 `더 잘 생각하고 더 잘 조사하는 단계`였다면,
- Phase 5는 그 능력을 `장시간, 반복, 장애 상황에서도 끊기지 않게 운영하는 단계`다.
- 즉 Phase 5의 핵심은 아래다.
  - 작업이 오래 걸려도 조용히 죽지 않는다.
  - provider 장애가 나도 전체 시스템이 흔들리지 않는다.
  - worker 재시작 이후에도 job 상태가 오염되지 않는다.
  - 자동 복구는 하되, 무한 재시도나 잘못된 회복은 막는다.
  - 운영자는 왜 멈췄고 왜 복구됐는지 바로 이해할 수 있다.

## 2. Why Phase 5 Exists
- 현재 시스템은 Phase 1~4를 거치며 아래 능력을 갖췄다.
  - workflow 기반 비선형 실행
  - memory DB 기반 retrieval/ranking
  - internal tool registry
  - vector retrieval opt-in
  - LangGraph planner shadow
- 하지만 `스스로 성장하는 개발 동료`가 되려면 생각만 잘하는 것으로는 부족하다.
- 실제 운영에서는 아래가 더 중요하다.
  - 5시간 이상 걸리는 장기 job
  - provider quota/latency/outage
  - worker restart
  - stale heartbeat
  - 반복 실패 job의 격리
  - 사람이 개입해야 하는 시점의 명확한 전환
- 따라서 Phase 5는 `더 agentic 하게 만드는 phase`가 아니라 `그 agent를 실제로 믿고 돌릴 수 있게 만드는 phase`다.

## 3. Scope Of Phase 5
- Phase 5는 아래 4개 축을 묶는다.
  - `job supervision`
  - `failure classification / retry policy`
  - `provider outage containment`
  - `human handoff / dead-letter safety`

## 4. Out Of Scope
- 아래는 Phase 5에서 직접 끝내지 않는다.
  - multi-worker claim model
  - distributed lock / external queue
  - zero-downtime / HA
  - backup / restore / archival durability
- 위 항목은 기존 계획대로 Phase 7~8 성격으로 남긴다.

## 5. Non-Negotiable Principles

### 5.1 Incremental Only
- 한 번에 worker/runtime 전체를 갈아엎지 않는다.
- 항상 `trace -> classify -> opt-in enforcement -> default` 순서로 간다.
- 기존 `orchestrator`, `worker_main`, `store`, `dashboard` 계약은 최대한 유지한다.

### 5.2 Small Slice Rule
- 한 슬라이스는 아래를 넘기지 않는다.
  - feature flag 1개
  - recovery/runtime adapter 1개
  - trace artifact 1개
  - operator API/UI 변경 최대 1개

### 5.3 Test-First Rule
- 모든 슬라이스는 최소 아래를 포함해야 한다.
  - unit test
  - failure simulation test
  - feature-flag off-path test
  - worker restart/recovery regression test
  - full `PYTHONPATH=. .venv/bin/pytest -q`
- 운영 phase 변경은 특히 아래가 중요하다.
  - stale heartbeat simulation
  - provider failure burst simulation
  - recovery count exhaustion simulation
  - interrupted node_runs cleanup simulation

### 5.4 Bounded Autonomy
- 자동 복구는 허용하되 무한 복구는 금지한다.
- 재시도는 반드시 budget, reason, 상태 전이 규칙을 가져야 한다.
- `needs_human`으로 떨어지는 기준은 코드/문서/테스트로 명시해야 한다.

### 5.5 Operator Visibility
- 복구 기능은 trace 없이 넣지 않는다.
- 운영자는 최소한 아래를 봐야 한다.
  - 왜 실패했는지
  - 왜 자동 복구됐는지
  - 왜 `needs_human`으로 전환됐는지
  - 같은 유형 실패가 얼마나 반복되는지

## 6. Current Baseline Snapshot

| Area | Status | Already Exists | Missing |
| --- | --- | --- | --- |
| Heartbeat loop | `PARTIAL` | shell command heartbeat callback, template runner heartbeat injection, `heartbeat_at` persisted | stage-level heartbeat policy normalization, long-run budget visibility |
| Stale running recovery | `PARTIAL` | stale `running` auto-requeue, `recovery_count`, `needs_human`, orphan running node interruption, structured `needs_human` summary, dead-letter retry flow, startup sweep trace baseline, restart-safe requeue reason baseline, restart-safe mismatch audit trail baseline, startup sweep audit history surface | restart-safe action drilldown |
| Queue hygiene | `PARTIAL` | orphan queued recovery, orphan running node cleanup, dead-letter state baseline, dead-letter retry action, operator note trail, dead-letter list/action | retry quarantine, dead-letter action drilldown |
| Recovery runtime | `PARTIAL` | failure assistant, recoverable heuristic, fix retry loop, test gate split, normalized failure classes baseline, class-aware retry policy, structured `needs_human`, provider cooldown baseline, dead-letter baseline, dead-letter retry trace, operator note trail | operator approval boundary, richer route fallback |
| Manual intervention path | `PARTIAL` | manual resume metadata, workflow resume state, structured `needs_human` handoff summary, dead-letter summary UI, dead-letter retry action, operator note trail | backlog-like approval flow for failed jobs |
| Provider outage handling | `PARTIAL` | workspace 단위 provider failure counter artifact, standard retry/hard gate counter 누적, admin metrics read surface, repeated `provider_timeout/tool_failure -> cooldown_wait` baseline, repeated burst -> `provider_quarantined` baseline, planner/reviewer route fallback baseline, `provider_circuit_open` baseline, provider outage history surface | richer route fallback, operator-facing action drilldown |
| Runtime supervision trace | `PARTIAL` | `_docs/RUNTIME_RECOVERY_TRACE.json`, stale auto-recovery trace, recovery runtime trace, job detail API read surface, `data/worker_startup_sweep_trace.json` baseline, recovery history summary UI, restart-safe audit surface | richer reason taxonomy, action drilldown |

## 7. What Already Exists

### 7.1 Heartbeat and stale recovery already exist
- long-running shell/test/AI command path에는 heartbeat callback이 연결돼 있다.
- worker는 stale `running` job을 감지하고 자동 requeue 또는 `needs_human` 전환을 수행한다.
- running node_runs도 stale recovery 때 `interrupted`로 정리된다.

### 7.2 Recovery runtime is already modularized
- failure assistant
- recoverable heuristic
- test gate split
- fix retry loop
- 즉, Phase 5는 recovery를 새로 만드는 phase가 아니라 `운영 정책을 명시적으로 올리는 phase`다.

### 7.3 Job state already has the right basic fields
- `heartbeat_at`
- `recovery_status`
- `recovery_reason`
- `recovery_count`
- `last_recovered_at`
- `manual_resume_*`
- 따라서 새 phase는 storage schema를 크게 바꾸기보다 policy와 trace를 먼저 붙이는 방향이 맞다.

### 7.4 Operator runtime inputs now exist
- admin dashboard에서 runtime input request를 등록하고 나중에 값을 제공할 수 있다.
- secret 값은 dashboard/API에서 마스킹되고, prompt-safe artifact와 shell/template env bridge로만 연결된다.
- 따라서 Phase 5에서는 `입력이 아직 없어서 막힌 job`을 failure classification / human handoff reason으로 다루는 단계가 자연스럽다.

### 7.5 Runtime recovery trace now exists
- worker stale recovery는 `_docs/RUNTIME_RECOVERY_TRACE.json`에 `stale_heartbeat` reason code와 `requeue / needs_human` decision을 남긴다.
- recovery runtime도 hard gate timeout / recovery not recoverable / recovery succeeded / recovery failed 판단을 같은 artifact에 남긴다.
- job detail API는 이 artifact를 `runtime_recovery_trace`로 읽어준다.
- 즉, Phase 5는 이제 `trace 없음` 상태가 아니라 `trace를 바탕으로 분류와 정책을 얹는 단계`로 넘어갔다.

### 7.6 Failure classification baseline now exists
- `app/failure_classification.py` 가 runtime failure evidence를 normalized class로 분류한다.
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
- runtime recovery trace event는 이제 `failure_class`를 함께 남긴다.
- jobs API와 job detail API도 failure class summary를 읽을 수 있다.
- 즉, Phase 5는 이제 `분류 코드 없음` 상태가 아니라 `분류 baseline 위에 mapping/policy를 얹는 단계`로 넘어갔다.

### 7.7 Stage/provider mapping now exists
- failure summary는 이제 `failure_class`만이 아니라 `provider_hint`와 `stage_family`도 같이 계산한다.
- runtime recovery trace event는 `provider_hint / stage_family`를 함께 남긴다.
- job detail API는 `runtime_recovery_trace.latest_provider_hint`, `latest_stage_family`, `failure_classification.provider_hint`, `failure_classification.stage_family`를 반환한다.
- jobs API도 `failure_provider_hint`, `failure_stage_family`를 반환하고 검색 haystack에 포함한다.
- 즉, Phase 5는 이제 `분류만 있는 상태`가 아니라 `분류 + provider/stage mapping` 위에서 UI/policy를 얹는 단계로 넘어갔다.

### 7.8 Dashboard visibility now exists
- dashboard jobs list는 failure classification hint를 보여준다.
- job detail은 `Failure Classification` 보드와 meta field를 통해 아래를 바로 노출한다.
  - `failure_class`
  - `provider_hint`
  - `stage_family`
  - `reason_code`
  - latest evidence reason
- 즉, Phase 5는 이제 `API에만 있는 상태`가 아니라 `운영자가 바로 읽을 수 있는 UI surface`까지 확보했다.

## 8. Why It Is Still Not Enough
- 지금은 “자동 복구가 된다” 수준이지 “어떤 실패를 어떻게 다르게 다루는가”는 아직 약하다.
- 예를 들어 아래가 아직 분리되지 않았다.
  - provider quota failure
  - provider timeout
  - repo/git conflict
  - flaky test
  - long-running stale heartbeat
  - invalid workflow / invalid config
- 현재는 이들이 대부분 `실패 -> recovery_count 증가 -> 재시도 또는 needs_human`으로 묶여 있다.
- baseline은 올라왔다.
  - standard retry / hard gate / worker stale recovery는 class-aware retry policy를 사용한다.
  - `needs_human` 전이는 이제 structured handoff summary를 남긴다.
  - job detail은 title / summary / recommended_actions / manual resume 권장 여부를 직접 보여준다.
- Phase 5는 이걸 `같은 실패처럼 다루지 않는 단계`다.

## 9. Phase 5 Architecture Direction

### 9.1 Keep
- `Orchestrator.process_next_job()`
- `worker_main.run_worker_forever()`
- existing job/store/node_runs contracts
- existing dashboard job detail and workflow detail surfaces

### 9.2 Add
- failure classification layer
- recovery policy layer
- provider outage guard
- dead-letter / human handoff layer
- recovery trace artifact

### 9.3 Do Not Do Yet
- distributed scheduler
- cross-worker leasing
- external message queue
- automatic multi-instance failover

## 10. Work Packages

### Phase 5-A. Runtime Supervision Baseline
- Goal:
  - job supervision을 `로그 기반 추측`이 아니라 `명시적 상태 전이`로 만든다.
- Small Slices:
  - `5-A1` recovery trace artifact
  - `5-A2` stage runtime budget metadata
  - `5-A3` stale reason normalization
- Deliverables:
  - `_docs/RUNTIME_RECOVERY_TRACE.json`
  - recovery reason codes
  - job detail runtime signal summary
- Success Criteria:
  - stale/timeout/requeue 판단이 artifact와 상태 필드에 함께 남는다.
  - 현재 상태: `5-A1` implemented, `5-A2~5-A3` pending

### Phase 5-B. Failure Classification
- Goal:
  - 실패를 공통 `exit_code != 0`에서 끝내지 않고 유형별로 분리한다.
- Target Classes:
  - `provider_quota`
  - `provider_timeout`
  - `provider_auth`
  - `stale_heartbeat`
  - `git_conflict`
  - `test_failure`
  - `tool_failure`
  - `workflow_contract`
  - `unknown_runtime`
- Small Slices:
  - `5-B1` classification codes only
  - `5-B2` stage/provider mapping
  - `5-B3` dashboard visibility
- Success Criteria:
  - failed job는 최소 1개의 normalized failure class를 가진다.
  - 현재 상태: `5-B1~5-B3` implemented

### Phase 5-C. Retry Policy Split
- Goal:
  - 실패 유형마다 다른 retry budget과 recovery path를 적용한다.
- Direction:
  - `test_failure`: fix loop 허용
  - `provider_timeout`: short retry 허용
  - `provider_quota`: 즉시 cooldown or needs_human
  - `git_conflict`: dedicated recovery path
  - `workflow_contract`: 즉시 needs_human
- Small Slices:
  - `5-C1` policy table only
  - `5-C2` budget enforcement
  - `5-C3` `needs_human` escalation rule hardening
- Success Criteria:
  - 같은 `recovery_count` 기준이 아니라 class-aware retry가 동작한다.
  - 현재 상태: `5-C1~5-C3` implemented baseline

### Phase 5-D. Provider Outage Containment
- Goal:
  - 한 provider 장애가 전체 worker thrash로 번지지 않게 막는다.
- Small Slices:
  - `5-D1` provider failure counters
  - `5-D2` cooldown window
  - `5-D3` route fallback policy
  - `5-D4` provider circuit-breaker baseline
- Examples:
  - Gemini timeout burst
  - Codex auth/quota failure burst
  - MCP server unavailable burst
- Success Criteria:
  - 같은 provider failure가 연속 발생하면 무한 재시도 대신 cooldown/alternate path/needs_human/quarantine/circuit-breaker로 전환된다.
  - 현재 상태: `5-D1~5-D4` implemented baseline

### Phase 5-E. Dead-Letter And Human Handoff
- Goal:
  - 반복 실패 job를 queue에 계속 되밀지 않고 안전하게 격리한다.
- Small Slices:
  - `5-E1` dead-letter state + reason
  - `5-E2` retry from dead-letter action
  - `5-E3` operator note + approval trail
- Deliverables:
  - dead-letter candidate list
  - recovery action history
  - human note preservation
- Success Criteria:
  - 반복 실패 job는 명시적 격리 상태로 보존되고, 재실행 이유도 남는다.
  - 현재 상태: `5-E1~5-E3` implemented baseline

### Phase 5-F. Worker Restart Safety
- Goal:
  - worker restart 이후 상태 오염 없이 자동 정리/재개가 가능해야 한다.
- Small Slices:
  - `5-F1` startup sweep trace
  - `5-F2` restart-safe requeue reason
  - `5-F3` running node/job mismatch audit
- Success Criteria:
  - worker 재시작 시 running 고착과 orphan node를 조용히 넘기지 않고 추적 가능하게 정리한다.
  - 현재 상태: `5-F1~5-F3` implemented baseline

### Phase 5-G. Minimal Operator Ops Surface
- Goal:
  - Phase 6 full observability 전이라도 운영자가 핵심 runtime 상태를 볼 수 있어야 한다.
- Small Slices:
  - `5-G1` dead-letter list / recovery history summary
  - `5-G2` provider circuit / startup audit history surface
  - `5-G3` drilldown filters for dead-letter and recovery actions
- Note:
  - rich charts/alerts는 Phase 6로 넘긴다.
- Success Criteria:
  - 운영자는 로그 파일을 뒤지지 않고도 실패/복구 상태를 이해할 수 있다.
  - 현재 상태: `5-G1` implemented baseline

## 11. Recommended Execution Order
1. `5-A1` recovery trace artifact
2. `5-B1` failure classification codes
3. `5-C1` retry policy table
4. `5-D1` provider failure counters
5. `5-D2` cooldown window
6. `5-E1` dead-letter state
7. `5-F1` startup sweep trace
8. `5-G1` minimal operator surface

## 12. Immediate Entry Gate
- Phase 5를 본격 시작하기 전 최소 조건은 아래다.
  - Phase 4 recovery shadow 방향이 문서화돼 있어야 한다.
  - Phase 4 self-growing bridge 우선순위와 충돌하지 않아야 한다.
  - current worker/recovery behavior를 artifact로 먼저 남길 수 있어야 한다.
- 즉, Phase 5의 첫 실제 구현은 `복구를 더 똑똑하게 만들기`보다 먼저 `복구를 더 잘 보이게 만들기`가 맞다.

## 13. Test Matrix

### 13.1 Unit
- failure classifier
- retry policy selector
- provider cooldown calculator
- dead-letter state transition

### 13.2 Integration
- stale heartbeat auto-recovery
- recovery count exhaustion -> `needs_human`
- provider outage burst -> cooldown
- worker restart sweep

### 13.3 Off-Path
- feature flag off면 기존 retry/recovery 동작이 그대로 유지돼야 한다.
- trace artifact off면 runtime 결과가 바뀌지 않아야 한다.

### 13.4 Full Regression
- 항상 마지막에 아래를 돈다.
```bash
PYTHONPATH=. .venv/bin/pytest -q
```

## 14. Exit Criteria
- stale/timeout/provider/test/git 유형이 최소한 normalized class로 나뉜다.
- recovery는 class-aware budget을 가진다.
- 반복 실패 job는 dead-letter 또는 `needs_human`으로 안전하게 격리된다.
- worker restart 이후 orphan/running 상태가 추적 가능하게 정리된다.
- operator는 recovery trace와 failure class를 UI/API에서 볼 수 있다.
- 장시간 job 운영 시 “조용히 멈췄는지 / 복구됐는지 / 사람 개입이 필요한지”가 명확해진다.

## 15. First Slice Recommendation
- Phase 5의 첫 구현은 `5-A1 Runtime Recovery Trace`였고, 이 슬라이스는 완료됐다.
- 그 다음 `5-B1 Failure Classification`도 구현됐다.
- 그 다음 `5-B2 stage/provider mapping`도 구현됐다.
- 그 다음 `5-B3 dashboard visibility`도 구현됐다.
- 그 다음 `failure transition runtime` 분리도 구현됐다.
- 그 다음 `5-C1 retry policy table`도 구현됐고, standard retry loop 기준 baseline enforcement가 들어갔다.
- 그 다음 `5-C2 retry budget enforcement`도 baseline 기준으로 구현됐고, hard gate와 worker stale recovery가 같은 selector를 보기 시작했다.
- 그 다음 `5-C3 needs_human hardening`도 baseline 기준으로 구현됐다.
- 그 다음 `5-D1 provider failure counters`도 baseline 기준으로 구현됐다.
- 그 다음 `5-D2 cooldown window`도 baseline 기준으로 구현됐다.
- 그 다음 `5-D3 alternate route fallback`도 baseline 기준으로 구현됐다.
- 그 다음 `5-E1 dead-letter state`도 baseline 기준으로 구현됐다.
- 그 다음 `5-E2 retry from dead-letter action`도 baseline 기준으로 구현됐다.
- 그 다음 `5-E3 operator note + approval trail`도 baseline 기준으로 구현됐다.
- 그 다음 `5-F1 startup sweep trace`도 baseline 기준으로 구현됐다.
- 그 다음 `5-F2 restart-safe requeue reason`도 baseline 기준으로 구현됐다.
- 그 다음 `5-F3 running node/job mismatch audit`도 baseline 기준으로 구현됐다.
- 그 다음 `5-D4 provider circuit-breaker baseline`도 구현됐다.
- 그 다음 `5-G1 dead-letter list / recovery history summary`도 구현됐다.
- 다음 우선순위는 `remaining runtime split`이다.
- 이유:
  - 이제 failure class / provider / stage evidence와 `needs_human` / `dead_letter` handoff shape, provider quarantine, provider circuit-breaker, planner/reviewer alternate route fallback, startup sweep trace baseline, restart-safe requeue reason baseline, running node/job mismatch audit baseline, dead-letter list, recovery history summary, provider outage history, startup sweep history, dead-letter / recovery action drilldown, recovery action groups, operator action trail은 충분히 보인다.
  - 다음은 runtime split과 self-growing bridge 효과성 검증 쪽이다.
