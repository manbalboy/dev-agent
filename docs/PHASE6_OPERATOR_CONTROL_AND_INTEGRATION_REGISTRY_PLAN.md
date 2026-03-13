# Phase 6 Operator Control And Integration Registry Plan

## 1. North Star

- Phase 6의 목표는 `운영자가 통제 가능한 개발 동료`를 만드는 것이다.
- Phase 5가 `오래 돌려도 덜 망가지게 만드는 단계`였다면,
- Phase 6은 `외부 서비스, 비밀키, 승인 경계, 운영자 개입`을 시스템 안에서 명시적으로 다루는 단계다.
- 특히 아래를 한 phase로 묶는다.
  - 남아 있는 Phase 5 operator control 잔여 항목
  - 서드파티 통합 레지스트리
  - runtime input / env bridge의 상위 계층
  - AI recommendation -> operator approval -> implementation 연결

## 2. Why Phase 6 Exists

- 지금 시스템은 이미 아래 baseline을 갖고 있다.
  - runtime recovery trace
  - failure classification / retry policy / dead-letter / provider containment
  - operator action trail
  - runtime input request / provide / masking
  - AI request draft recommendation
- 하지만 실제 제품 개발에서는 아래가 아직 수작업에 가깝다.
  - `Google Maps`, `Supabase`, `Stripe`, `Firebase`, `Sentry` 같은 서드파티 도입 기준
  - 어떤 env가 필요한지
  - 어떤 가이드와 코드 패턴을 따라야 하는지
  - 어떤 시점에 운영자 승인이 필요한지
  - 외부 통합이 빠져서 job이 막혔을 때 어떤 handoff로 보여줄지
- 즉, 지금은 `실패 운영`은 꽤 강해졌지만 `통합 운영`은 아직 시스템 계약으로 올라오지 않았다.

## 3. What Phase 6 Combines

Phase 6은 아래 둘을 합친다.

### 3.1 Remaining Phase 5 Control-Plane Work

- failed / dead-letter / quarantine / circuit-open 상태의 operator action boundary
- restart-safe action drilldown의 추가 고도화
- provider fallback / route action의 운영자 가시성 통합
- `needs_human` 사유 중 `missing integration input` 계열의 정규화

### 3.2 Third-Party Integration Registry

- 통합 이름과 설명
- 필요한 env / optional env
- 앱 타입별 도입 규칙
- 운영자 가이드 / 구현 가이드 / 검증 가이드
- AI가 통합 도입을 `자동 사용`이 아니라 `검토/추천`하도록 만드는 기준

## 4. Non-Negotiable Principles

### 4.1 Approval First

- AI가 외부 서비스를 마음대로 도입하면 안 된다.
- 기본 흐름은 반드시 아래 순서다.
  1. 도입 검토
  2. 운영자 승인
  3. 입력/키 제공
  4. 구현/검증

### 4.2 Secret-Safe By Default

- 실제 키 값은 prompt, log, UI payload에 직접 넣지 않는다.
- 키는 runtime input registry와 env bridge를 통해서만 전달한다.
- dashboard에서는 항상 masking 상태로만 보인다.

### 4.3 Guide-Driven Integration

- AI는 라이브러리를 매번 임의 선택하지 않는다.
- 통합 레지스트리에 등록된 가이드와 팀 규칙을 우선 따른다.
- 즉 `어떤 SDK를 쓰는가`도 운영자가 통제할 수 있어야 한다.

### 4.4 Small Slice Rule

- 한 번에 registry + UI + approval + env + prompt injection을 모두 default on 하지 않는다.
- 항상 `schema -> read UI -> write UI -> recommendation -> approval -> runtime injection` 순서로 간다.

### 4.5 Test-First Rule

- 모든 슬라이스는 최소 아래를 포함한다.
  - unit test
  - dashboard API contract test
  - secret masking test
  - prompt off-path test
  - full `PYTHONPATH=. .venv/bin/pytest -q`

## 5. Current Baseline Snapshot

| Area | Status | Already Exists | Missing |
| --- | --- | --- | --- |
| Runtime input registry | `STRONG` | request/provide/masking/env bridge | integration-level binding policy |
| Operator action trail | `PARTIAL` | dead-letter retry, note trail, recovery action groups, integration approval action baseline, integration approval trail baseline | richer approval drilldown + cross-job usage audit |
| Failure operations | `PARTIAL` | dead-letter/quarantine/circuit-open/provider cooldown | missing integration input classification |
| Prompt control | `PARTIAL` | mobile app mode, operator input hints, diagnosis loop | integration-specific guide injection |
| Third-party governance | `PARTIAL` | registry schema/storage, admin read/list UI, approval boundary | usage audit |

## 6. Phase 6 Data Model Direction

### 6.1 Integration Registry Entry

- `integration_id`
- `display_name`
- `category`
- `supported_app_types`
- `tags`
- `required_env_keys`
- `optional_env_keys`
- `operator_guide_markdown`
- `implementation_guide_markdown`
- `verification_notes`
- `approval_required`
- `enabled`

### 6.2 Integration Binding

- registry 자체와 별개로 아래 바인딩이 필요하다.
  - `app_code` 기준 기본 바인딩
  - `job_id` 기준 임시 바인딩
  - `requested_by`
  - `approved_by`
  - `approved_at`
  - `status`

### 6.3 Usage Trail

- 어떤 job이 어떤 integration을 검토했는지
- 어떤 note로 승인됐는지
- 실제 env가 연결됐는지
- 구현 단계에서 어떤 가이드가 prompt에 주입됐는지

## 7. Example: Google Maps

운영자는 `Google Maps` 항목을 아래처럼 등록할 수 있어야 한다.

- 이름: `Google Maps`
- 카테고리: `mapping`
- 앱 타입: `web`, `app`
- required env:
  - `GOOGLE_MAPS_API_KEY`
- 구현 가이드:
  - 웹에서는 어떤 loader/SDK를 쓰는지
  - RN에서는 어떤 패키지와 권한 설정을 쓰는지
- 검증 기준:
  - 지도 로딩
  - 기본 핀 표시
  - API key 누락 시 graceful fallback

그 다음 흐름은 아래가 된다.

1. planner가 요구사항에서 `지도 / 위치 / 매장 찾기 / 경로`를 감지한다.
2. `google_maps`를 `도입 검토 후보`로 추천한다.
3. 운영자가 승인한다.
4. 운영자가 key를 runtime input에 제공한다.
5. coder는 등록된 가이드와 env 이름을 기준으로 구현한다.
6. 테스트/리뷰는 `Google Maps integration expected`를 기준으로 검증한다.

## 8. Work Packages

### Phase 6-A. Integration Registry Baseline

- Goal:
  - 통합 정의를 코드 밖의 registry로 만든다.
- Small Slices:
  - `6-A1` registry schema + storage baseline
  - `6-A2` admin read API + list UI
  - `6-A3` admin create/update/delete API
- Success Criteria:
  - 운영자가 `Google Maps` 같은 통합 항목을 등록/조회할 수 있다.
- 현재 상태:
  - `6-A1 registry schema + storage baseline` implemented
  - `6-A2 admin read/list UI` implemented
  - JSON/SQLite store, admin API baseline, admin list/detail UI, normalization/runtime test까지 들어감
  - 다음 우선순위는 `6-B1 integration -> required runtime input link`

### Phase 6-B. Runtime Input Bridge Upgrade

- Goal:
  - 통합 항목과 runtime input request를 연결한다.
- Small Slices:
  - `6-B1` integration -> required runtime input link
  - `6-B2` missing integration input reason surface
  - `6-B3` env bridge policy hardening
- Success Criteria:
  - 특정 통합이 필요한데 key가 없으면 명시적으로 `needs_human` 또는 준비 대기 상태로 보인다.
- 현재 상태:
  - `6-B1 integration -> required runtime input link` implemented
  - `6-B2 missing integration input reason surface` implemented
  - `6-B3 env bridge policy hardening` implemented
  - integration registry detail/list에서 required env별 `provided / requested / missing` linkage summary와 `준비 완료 / 승인 대기 / 입력 요청됨 / 운영자 입력 필요` reason surface를 확인할 수 있음
  - integration-linked env는 approval/input readiness가 맞지 않으면 runtime env bridge에 들어가지 않고 `blocked_inputs`, `blocked_env_vars`로 남음
  - job detail operator inputs surface에서 정책상 차단된 입력과 차단 사유를 바로 볼 수 있음
  - integration control-plane 자체는 baseline이 닫혔고, 인접 과제로 `self-growing bridge` 효과성을 admin/operator surface, 최근 7일 추세, failure cluster follow-up 효과, 재발 감소/유지/증가 집계, regressed/insufficient baseline facet까지 연결했다.
  - 다음 우선순위는 `remaining runtime split`, `enterprise 운영 계층 보강`, `dashboard write action/service 축소`

### Phase 6-C. AI Recommendation And Approval

- Goal:
  - AI가 통합을 자동 사용하지 않고 추천/검토하게 만든다.
- Small Slices:
  - `6-C1` planner recommendation draft
  - `6-C2` operator approve/reject action
  - `6-C3` approval trail
- Success Criteria:
  - AI recommendation과 operator approval이 분리된다.
- 현재 상태:
  - `6-C1 planner recommendation draft` implemented
  - `6-C2 operator approve/reject action` implemented
  - `6-C3 approval trail` implemented
  - planner는 `_docs/INTEGRATION_RECOMMENDATIONS.json`을 통해 통합을 `도입 검토 후보`로만 추천하고, approval 전 자동 사용은 하지 않음
  - integration registry entry는 이제 `approval_status`, `approval_note`, `approval_updated_at`, `approval_updated_by`를 저장한다
  - operator는 admin surface/API에서 integration을 `승인 / 보류 / 다시 검토` 상태로 바꿀 수 있다
  - integration registry entry는 이제 append-only `approval_trail`을 저장하고, 대시보드 detail surface에서 최근 승인/보류/재검토 이력을 바로 보여준다
  - recommendation payload는 `required_input_summary`, `input_readiness_status`, `input_readiness_reason`, `approval_status`, `approval_note`를 같이 남긴다
  - recommendation payload는 `approval_trail_count`, `latest_approval_action`도 같이 남긴다
  - rejected candidate는 `operator_rejected`, approved+ready candidate는 `approved_candidate`로 정규화된다
  - 다음 우선순위는 `remaining runtime split`

### Phase 6-D. Guide Injection

- Goal:
  - 승인된 통합의 구현 가이드가 planner/coder/reviewer 입력으로 들어간다.
- Small Slices:
  - `6-D1` prompt-safe guide summary
  - `6-D2` code pattern/snippet hint
  - `6-D3` verification checklist injection
- Success Criteria:
  - AI는 임의 검색보다 등록된 팀 가이드를 우선 참고한다.
- 현재 상태:
  - `6-D1 prompt-safe guide summary` implemented
  - `6-D2 code pattern/snippet hint` implemented
  - `6-D3 verification checklist injection` implemented
  - 승인된 통합만 `_docs/INTEGRATION_GUIDE_SUMMARY.md`로 요약되어 planner/coder/reviewer prompt에 주입된다.
  - 승인된 통합만 `_docs/INTEGRATION_CODE_PATTERNS.md`로 요약되어 planner/coder/reviewer prompt에 주입된다.
  - 승인된 통합만 `_docs/INTEGRATION_VERIFICATION_CHECKLIST.md`로 요약되어 planner/coder/reviewer prompt에 주입된다.
  - secret 값은 제외되고 env var 이름, 승인 상태, 입력 준비 상태, 운영/구현/검증 가이드 요약만 포함된다.
  - 구현 가이드의 코드 패턴, redacted snippet, 검증 힌트가 prompt-safe 형태로 분리된다.
  - verification notes는 prompt-safe verification checklist/self-check 기준으로 분리된다.
  - `operator_rejected` / `pending` 통합은 guide summary에서 제외된다.
  - 다음 우선순위는 `remaining runtime split`

### Phase 6-E. Carry-Over From Phase 5

- Goal:
  - late Phase 5의 operator control 잔여 항목을 Phase 6 control plane에서 마무리한다.
- Small Slices:
  - `6-E1` failed job operator approval boundary
  - `6-E2` richer restart-safe action drilldown
  - `6-E3` provider fallback / route action surface 통합
- Success Criteria:
  - 실패 운영과 통합 운영이 따로 놀지 않는다.
- 현재 상태:
  - `6-E1 failed job operator approval boundary` implemented
  - failed / needs_human / dead_letter / provider_quarantined / provider_circuit_open job detail은 이제 `integration_operator_boundary` payload를 통해 통합 승인/입력 부족 때문에 막힌 후보와 권장 조치를 보여준다.
  - integration recommendation artifact와 blocked/pending runtime input을 함께 읽어 `approval_and_input_required`, `approval_required`, `input_required` 경계를 정규화한다.
  - 다음 우선순위는 `remaining runtime split`

### Phase 6-F. Audit And Health

- Goal:
  - 어떤 통합이 실제로 얼마나 쓰였는지, 어디서 막혔는지 본다.
- Small Slices:
  - `6-F1` integration usage trail
  - `6-F2` missing-input / auth / quota facet
  - `6-F3` integration health summary
- Success Criteria:
  - 운영자는 “무슨 통합이 필요한데 왜 막혔는지”를 파일 로그 없이 본다.
- 현재 상태:
  - `6-F1 integration usage trail` implemented
  - `6-F2 missing-input / auth / quota facet` implemented
  - `6-F3 integration health summary` implemented
  - planner/coder/reviewer prompt 주입 시 승인된 통합과 blocked env를 `_docs/INTEGRATION_USAGE_TRAIL.json`로 append-only 기록한다.
  - job detail은 `integration_usage_trail`로 어떤 통합이 실제 prompt에 주입됐는지 recent event 기준으로 보여준다.
  - job detail은 `integration_health_facets`를 통해 missing input, provider auth, provider quota blocker를 한 화면에서 보여준다.
  - usage trail, operator boundary, failure classification, log auth/quota hint를 합쳐 facet를 계산한다.
  - admin metrics는 이제 `integration_health_summary`를 통해 승인 상태, 준비 상태, 최근 사용 통합, 차단 경계, 자주 막히는 env, 최근 막힌 작업을 한 화면에서 보여준다.
  - 다음 우선순위는 `remaining runtime split`

## 9. Recommended Next Slice

- 다음 가장 작은 구현은 `remaining runtime split`이다.
- 이유:
  - recommendation, approval trail, prompt-safe guide summary, env bridge policy hardening, code pattern/snippet hint, verification checklist injection, failed job operator approval boundary, integration usage trail, missing-input/auth/quota facet, integration health summary까지 control-plane baseline은 올라왔다.
  - 다음 큰 리스크는 통합 운영보다 여전히 남아 있는 runtime split과 구조 리스크다.

## 10. Success Criteria

- 운영자가 `Google Maps` 같은 통합 항목을 dashboard에 등록할 수 있다.
- AI는 기능 요구를 보고 해당 통합을 `검토 후보`로만 제안한다.
- 운영자 승인 없이 통합이 자동 활성화되지 않는다.
- 실제 키 값은 프롬프트/로그/UI에 노출되지 않는다.
- 승인된 통합은 env bridge와 guide injection을 통해 구현에 반영된다.
- 실패 job가 `integration missing`, `approval pending`, `provider auth`, `quota`를 구분해서 보여준다.
- 실패 job detail은 `integration_operator_boundary`를 통해 approval/input 부족 때문에 막힌 통합 후보와 권장 조치를 직접 보여준다.
- 실패 job detail은 `integration_usage_trail`, `integration_health_facets`를 통해 실제 사용 통합과 blocker facet를 함께 보여준다.

## 11. What Phase 6 Is Not

- AI가 마음대로 외부 서비스를 붙이는 phase가 아니다.
- secret manager를 외부 시스템으로 완전 교체하는 phase도 아니다.
- enterprise billing/compliance를 한 번에 끝내는 phase도 아니다.

Phase 6은 `운영자가 통제하는 범위 안에서 외부 통합을 시스템화하는 단계`다.
