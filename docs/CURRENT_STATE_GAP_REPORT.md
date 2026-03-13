# Current State Gap Report

기준 시각: 2026-03-13 (KST)

## 1. 평가 대상

- 이 문서는 현재 코드와 문서를 기준으로, 이 시스템이 `스스로 성장하는 24시간 개발 동료` 목표에 얼마나 가까운지 냉정하게 판정하는 문서다.
- 여기서 말하는 목표 상태는 단순히 이슈를 처리해 PR을 만드는 자동화가 아니다.
- 아래를 동시에 만족해야 한다.
  - 제품/코드/실패 상태를 스스로 읽는다.
  - 필요한 tool과 memory를 안전하게 쓴다.
  - 반복 실패를 다음 행동으로 연결한다.
  - 장시간 실행과 provider 이상 상황에서도 조용히 망가지지 않는다.
  - 운영자가 로그 파일을 뒤지지 않고도 왜 그런 판단이 나왔는지 이해할 수 있다.

## 2. 한 줄 판정

- 현재 상태는 `강한 기반을 가진 고급 프로토타입`이다.
- 하지만 아직 `믿고 오래 돌릴 수 있는 self-growing companion`이라고 부르기에는 이르다.
- 가장 큰 이유는 아래 3개다.
  - 핵심 런타임 파일이 아직 너무 크다.
  - Phase 4는 인상적인 shadow/opt-in 기능이 많지만 primary 운영 전환은 제한적이다.
  - Phase 5 운영 신뢰성은 아직 본격 시작 전 단계다.

## 3. 현재 점수표

| Area | Status | 냉정한 판정 | 가장 큰 부족분 |
| --- | --- | --- | --- |
| Product pipeline / workflow spine | `STRONG` | 자동 실행 엔진의 뼈대는 충분히 강하다. | 더 큰 문제는 기능이 아니라 운영 신뢰성과 구조 복잡도다. |
| Self-improvement loop | `PARTIAL` | 리뷰와 개선 산출물은 많지만, 품질 상승이 항상 보장되지는 않는다. | improvement 결과의 장기 효과 측정과 strategy 전환 기준이 약하다. |
| Memory runtime | `PARTIAL` | DB-backed memory와 ranking/backlog까지 갔다. | planner/reviewer/coder 전반에 semantic retrieval이 넓게 퍼지지 않았다. |
| Agentic runtime (tool/vector/graph) | `PARTIAL` | MCP/Qdrant/LangGraph가 이미 들어와 있고 방향은 맞다. | 아직 shadow/opt-in 비중이 높고, 운영자가 체감하는 primary 가치로 완전히 승격되지는 않았다. |
| Self-growing bridge | `PARTIAL` | backlog -> follow-up job까지 연결됐고, `SELF_GROWING_EFFECTIVENESS.json` baseline과 job detail/admin summary, 최근 7일 추세, 앱별 효과 분포, failure cluster 기반 follow-up 효과, 재발 감소/유지/증가 집계, regressed reason 분포, insufficient baseline reason 분포, 최근 회귀/기준 부족 사례까지 올라왔다. | 아직 operator approval 의존이 크고, 장기 기간에서 재발 감소가 안정적으로 유지되는지 증명하는 운영 지표는 더 필요하다. |
| Long-running operations | `PARTIAL` | heartbeat stale 구조 문제와 recovery trace artifact, normalized failure class baseline, stage/provider mapping, operator visibility, class-aware retry policy, structured `needs_human` handoff baseline, provider failure counters, provider cooldown window, provider quarantine baseline, provider circuit-breaker baseline, planner/reviewer alternate route fallback baseline, worker startup sweep trace baseline, restart-safe requeue reason baseline, running node/job mismatch audit baseline, dead-letter baseline, dead-letter 재큐잉 액션, operator note trail, dead-letter list, recovery history summary, provider outage history, startup sweep history, dead-letter/recovery action drilldown, recovery action groups, operator action trail까지는 올라왔다. | 남은 runtime 분리와 durable/enterprise 운영 계층이 아직 약하다. |
| Operator UX / dashboard | `PARTIAL` | 운영자 입력, diagnosis trace, 비교 뷰, structured `needs_human` handoff 보드까지 들어갔다. | 화면 복잡도와 정보 구조가 여전히 높고, 초심자 친화성은 개선 중이다. |
| Maintainability | `PARTIAL` | 일부 모듈 분리는 꽤 진행됐고, failure transition/runtime surface도 분리됐다. issue/spec stage helper, product review, artifact I/O, design governance, memory retrieval/context/shadow/ingest helper, memory quality/feedback/ranking helper, structured memory/convention helper, integration recommendation/helper/usage helper, log/heartbeat helper, stop-signal/agent-profile/job lookup helper, job dispatch/single-attempt helper, track/escalation/recovery toggle helper, template variable/fallback artifact helper, tool/search/evidence helper, workflow binding/context helper, commit stage/helper 본문, legacy fixed pipeline 본문, repository/stage support helper, orchestrator context/helper bridge, dashboard job action/service helper도 런타임 쪽으로 흡수됐다. product-review operating principle alignment도 runtime 쪽으로 흡수됐다. | [app/orchestrator.py](../app/orchestrator.py) 가 `2605`라인, [app/dashboard.py](../app/dashboard.py) 는 `3656`라인이다. 다만 핵심 구조 리스크는 여전히 orchestrator 쪽이 더 크다. |
| Production-readiness baseline | `PARTIAL` | CI, SECURITY, CONTRIBUTING, hygiene 검사까지 들어갔다. | 실제 시크릿 로테이션, LICENSE 결정, durable backend는 아직 남아 있다. |

## 4. 지금 분명히 잘하고 있는 것

- 고정 단계형 파이프라인을 넘어 workflow/node runtime으로 올라간 것은 명확한 강점이다.
- memory DB, ranking, backlog candidate, follow-up bridge까지 간 것은 단순 자동화 툴 수준을 넘는다.
- MCP/Qdrant/LangGraph를 `전면 교체`가 아니라 `shadow -> opt-in`으로 붙인 판단은 맞다.
- operator tooling, diagnosis trace, runtime input registry 등 운영자 가시성도 꾸준히 올라갔다.
- production-readiness 기준도 예전보다 훨씬 나아졌다.
  - 최소 CI
  - SECURITY / CONTRIBUTING
  - 저장소 위생 검사
  - 위험 플래그 opt-in
- Phase 5-A1도 시작됐다.
  - `_docs/RUNTIME_RECOVERY_TRACE.json`
  - stale auto-recovery / recovery runtime decision trace
  - job detail API read surface
- Phase 5-B1도 올라왔다.
  - `app/failure_classification.py`
  - runtime recovery trace event-level `failure_class`
  - job detail / jobs API failure class summary
- Phase 5-B2도 올라왔다.
  - `provider_hint / stage_family` mapping
  - runtime recovery trace latest provider/stage summary
  - jobs API search evidence 확장
- Phase 5-B3도 올라왔다.
  - dashboard list에 failure classification hint
  - job detail debug/meta에 failure classification board
- Phase 5-C3도 baseline 기준으로 올라왔다.
  - `needs_human` 상태가 이제 structured operator handoff summary를 가진다.
  - standard retry / hard gate / stale recovery가 같은 handoff shape를 남긴다.
  - job detail API/UI가 왜 사람 개입이 필요한지와 권장 조치를 보여준다.
- Phase 5-D2도 baseline 기준으로 올라왔다.
  - workspace 단위 `PROVIDER_FAILURE_COUNTERS.json` artifact가 생겼다.
  - standard retry / hard gate가 provider failure counter를 누적한다.
  - admin metrics가 provider별 recent failure count를 읽기 시작했다.
  - 반복 `provider_timeout/tool_failure`는 `cooldown_wait`로 전이되기 시작했다.
- Phase 5-E1도 baseline 기준으로 올라왔다.
  - 일반 최종 실패는 `failed + recovery_status=dead_letter`로 표준화되기 시작했다.
  - runtime recovery trace는 `dead_letter_summary`를 남긴다.
  - job detail API/UI가 dead-letter 격리 상태를 바로 보여준다.

## 5. 아직 목표지점이 아닌 이유

### 5.1 구조 리스크가 아직 너무 크다

- 가장 큰 기술 부채는 [app/orchestrator.py](../app/orchestrator.py) 다.
- 이 파일 하나에 workflow 실행, provider 실행, recovery, memory bridge, git/PR, stage policy가 여전히 과도하게 몰려 있다.
- 지금 기능이 더 늘면 `새 기능이 안 되는 문제`보다 `기존 기능을 안전하게 못 바꾸는 문제`가 먼저 터질 가능성이 높다.
- 다만 리팩터는 실제로 진행 중이다.
  - `assistant_runtime`
  - `agent_config_runtime`
  - `summary_runtime`
  - `content_stage_runtime`
  - `review_fix_runtime`
  - `planner_runtime`
  - `implement_runtime`
  - `workflow_node_runtime`
  - `workflow_pipeline_runtime`
  - `provider_runtime`
  - `preview_runtime`
  - `app_type_runtime`
  - `product_definition_runtime`
  - `improvement_runtime`
  - `ux_review_runtime`
  - `workflow_resolution_runtime`
  - `docs_snapshot_runtime`
  - `dashboard_job_runtime`
  - `dashboard_runtime_input_runtime`
  - `dashboard_admin_metrics_runtime`
- 따라서 지금 판단은 `리팩터 착수 전`이 아니라 `리팩터 진행 중이지만 아직 목표 수치가 멀다`에 가깝다.

### 5.2 Phase 4는 많이 했지만 아직 운영 중심 value가 덜 닫혔다

- tool/vector/graph는 모두 방향이 맞다.
- 하지만 냉정하게 말하면 현재는 `기능이 있다`와 `운영에서 주력으로 믿고 쓴다` 사이가 아직 멀다.
- 특히 아래가 아직 약하다.
  - vector retrieval broader rollout
  - MCP live operator validation
  - diagnosis trace의 장기 효과성 측정
  - backlog approval UX 완성도

### 5.3 Phase 5가 약해서 장시간 운영 신뢰성이 부족하다

- 현재 시스템은 `스스로 성장하는 개발 동료`보다 `잘 만든 장기 실험기`에 더 가깝다.
- 이유는 failure를 아직 충분히 분리해서 다루지 못하기 때문이다.
- 특히 아래가 중요했고, 이제 baseline code는 붙었다.
  - `provider_quota`
  - `provider_timeout`
  - `provider_auth`
  - `git_conflict`
  - `workflow_contract`
  - `stale_heartbeat`
- 하지만 지금은 이 분류와 정책이 운영자 작업면까지 충분히 정리되지는 않았다.

### 5.4 self-growing loop가 아직 완전히 자율적이지 않다

- backlog candidate 생성, approval, follow-up job enqueue까지는 왔다.
- 하지만 아직 `진짜로 시스템이 품질 하락을 안정적으로 감지하고, 다음 실행으로 연결하고, 그 효과를 누적 학습한다`고 보기는 어렵다.
- 즉 현재는 `self-growing의 기초 구조`는 있지만 `지속적으로 믿고 맡길 수준의 자기 성장 엔진`은 아니다.

### 5.5 durable runtime은 거의 미래 작업으로 남아 있다

- queue/store는 아직 단일 호스트 친화적이다.
- backup/restore, cleanup, durable backend, HA는 아직 본게임 전 단계다.
- 따라서 `24시간 운영 가능`을 주장하려면 Phase 5와 7이 더 닫혀야 한다.

## 6. 지금 하지 말아야 할 것

- provider 종류를 더 늘리는 일
- shadow trace만 늘리고 promotion 기준 없이 방치하는 일
- dashboard에 새 카드/새 패널만 계속 추가하는 일
- durable runtime이 준비되기 전에 HA/무중단을 먼저 만지는 일
- 운영 정책이 없는데 autonomy를 더 넓히는 일

## 7. 추천 우선순위

### Priority 1. remaining runtime split 잔여 정리

- 목적:
- 다음 enterprise 운영 슬라이스를 안전하게 받기 전에 오케스트레이터 잔여 helper를 더 줄이기
- 바로 다음 작은 슬라이스:
  1. residual runtime/helper split
  2. durable/enterprise 운영 계층 보강
  3. dashboard write action/service 잔여 축소

### Priority 2. 구조 리스크 축소

- 목적:
  - 다음 phase 작업을 안전하게 받기 위한 기반 정리
- 바로 필요한 것:
  1. [app/orchestrator.py](../app/orchestrator.py) 의 다음 구조 분리
  2. [app/dashboard.py](../app/dashboard.py) write action/service 축소
  3. self-growing bridge 장기 효과 집계
  4. dashboard/job detail에서 failure class UI 노출
  5. class-aware retry policy

### Priority 3. operator control 정교화

- 목적:
  - 운영자가 follow-up / recovery / integration 승인 흐름을 더 조밀하게 조작하게 만들기
- 바로 필요한 것:
  1. backlog approval UX 보강
  2. integration health와 failure handoff의 교차 drilldown
  3. dashboard write action/service 잔여 축소

### Priority 4. durable runtime 준비

- 목적:
  - Phase 7 진입 전에 단일 호스트 파일 기반 런타임의 한계를 명확히 줄이기
- 바로 필요한 것:
  1. backup / restore runbook과 상태 점검
  2. workspace cleanup policy
  3. queue/store 추상화 경계 점검

## 8. 목표 도달 판정 기준

- 아래가 되기 전에는 `목표지점 도달`이라고 부르지 않는다.
  - [app/orchestrator.py](../app/orchestrator.py) 의 핵심 책임이 더 잘게 분리된다.
  - failed job이 normalized failure class를 가진다.
  - retry / cooldown / needs_human 전이가 class-aware하게 동작한다.
  - tool/vector/graph path 중 최소 일부가 shadow가 아니라 안정적 primary path로 운영된다.
  - follow-up job / backlog / diagnosis trace의 장기 효과가 대시보드에서 읽힌다.
  - 운영자가 시크릿/설정/실패 대응을 문서와 UI만으로 처리할 수 있다.

## 9. 현재 결론

- 지금 이 프로젝트는 `목표지점으로 가는 방향은 맞다`.
- 하지만 아직 `도달했다`고 말하면 과장이다.
- 현재 가장 필요한 것은 새 기능 추가보다 아래 두 가지다.
  - 구조 리스크를 낮추는 리팩터링
  - Phase 5 운영 신뢰성 작업
