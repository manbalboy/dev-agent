# Phase 1 Exit Criteria

## Purpose
- Phase 1의 목적은 `자동 코딩 시스템`을 `제품 정의 -> 구현 -> 리뷰 -> 개선` 파이프라인을 갖춘 자기 고도화 개발 에이전트의 코어 프레임으로 끌어올리는 것이다.
- 이 문서는 Phase 1 종료 여부를 판단하는 기준과 현재 충족 상태를 기록한다.

## Exit Decision
- Decision: `PASS`
- Status: `Phase 1 종료 가능`
- Notes:
  - 제품 개발형 파이프라인, 품질 평가 체계, 반복 개선 루프, workflow 기반 실행, recovery/resume, control-flow node 실행까지 연결되었다.
  - 남은 항목은 Phase 1 미완이 아니라 Phase 2의 적응형 메모리/운영 고도화 성격이다.

## Exit Criteria

### 1. Product-First Pipeline Exists
- 요구사항:
  - 구현 전에 제품 정의 산출물이 생성되어야 한다.
  - 최소 산출물: `PRODUCT_BRIEF.md`, `USER_FLOWS.md`, `MVP_SCOPE.md`, `ARCHITECTURE_PLAN.md`, `SCAFFOLD_PLAN.md`
  - 구현/수정 단계 진입 전 hard gate가 이 산출물을 검증해야 한다.
- Current status: `PASS`
- Evidence:
  - 제품 정의 단계 실행
  - product-definition hard gate 2곳 적용
  - fallback + retry + contract validation 동작

### 2. MVP-First / Design-First Enforcement Exists
- 요구사항:
  - 설계 문서가 약하면 구현을 시작하지 않는다.
  - MVP 범위와 out-of-scope가 명시되어야 한다.
- Current status: `PASS`
- Evidence:
  - `_ensure_product_definition_ready()` 구현
  - `MVP_SCOPE.md` acceptance gates 적용
  - operating principles가 planner/coder/reviewer prompt에 주입됨

### 3. Review System Produces Structured Quality Signals
- 요구사항:
  - 단순 성공/실패가 아니라 품질 점수와 근거를 생성해야 한다.
  - 최소 평가 축:
    - code_quality
    - architecture_structure
    - maintainability
    - usability
    - ux_clarity
    - test_coverage
    - error_state_handling
    - empty_state_handling
    - loading_state_handling
- Current status: `PASS`
- Evidence:
  - `PRODUCT_REVIEW.json`
  - `PRODUCT_REVIEW.schema.json`
  - artifact health / category evidence / evidence summary / principle alignment 포함

### 4. Improvement Loop Is Real, Not Decorative
- 요구사항:
  - `review -> backlog -> plan -> next tasks`가 실제 다음 실행에 연결되어야 한다.
  - loop guard가 반복/정체/하락을 감지해야 한다.
- Current status: `PASS`
- Evidence:
  - `REVIEW_HISTORY.json`
  - `IMPROVEMENT_BACKLOG.json`
  - `IMPROVEMENT_LOOP_STATE.json`
  - `IMPROVEMENT_PLAN.md`
  - `NEXT_IMPROVEMENT_TASKS.json`
  - planner/coder/fix가 next tasks와 strategy를 직접 소비

### 5. Workflow Execution Is Configurable and Persisted
- 요구사항:
  - workflow가 JSON으로 저장/검증/선택되어야 한다.
  - job은 `job > app > default` 우선순위로 workflow를 결정해야 한다.
- Current status: `PASS`
- Evidence:
  - `config/workflows.json`
  - workflow schema API / save API / default selection API
  - app별 workflow_id / job별 workflow override

### 6. Node-Level Runtime Trace Exists
- 요구사항:
  - workflow node 실행 이력이 attempt 단위로 남아야 한다.
  - failure/success, error message, agent profile이 기록되어야 한다.
- Current status: `PASS`
- Evidence:
  - `node_runs`
  - job detail runtime signals
  - workflow detail UI에서 node run 확인 가능

### 7. Recovery and Resume Exist
- 요구사항:
  - failed safe node resume
  - stale running auto recovery
  - manual resume / manual full rerun
- Current status: `PASS`
- Evidence:
  - safe resume 판단
  - `running` stale recovery
  - manual workflow retry API + detail UI

### 8. Quality Trend and Repo Maturity Exist
- 요구사항:
  - repo 성숙도와 품질 추세를 계산해야 한다.
  - category-level score history와 trend를 보존해야 한다.
- Current status: `PASS`
- Evidence:
  - `REPO_MATURITY.json`
  - `QUALITY_TREND.json`
  - `REVIEW_HISTORY.json`에 `scores` 저장
  - persistent low / stagnant / category delta 계산

### 9. Control-Flow Nodes Execute
- 요구사항:
  - workflow는 단순 직선 실행이 아니라 최소한 아래 control-flow를 지원해야 한다.
    - `if_label_match`
    - `loop_until_pass`
  - `success/failure/always` edge를 실제로 타야 한다.
- Current status: `PASS`
- Evidence:
  - edge-driven workflow executor
  - label branch / same-attempt loop tests
  - workflow editor에서 control-node metadata 설정 가능

### 10. Dashboard and Settings Are Operationally Usable
- 요구사항:
  - 대시보드 root가 타임아웃 없이 로드된다.
  - jobs/settings/assistant/workflow editor가 기본 운영 가능한 수준이어야 한다.
- Current status: `PASS`
- Evidence:
  - root shell render 분리
  - jobs filters/pagination
  - runtime signals / recovery / strategy 표시
  - workflow editor 저장/검증/metadata 설정
  - workflow diff/preview 요약 제공

## Phase 1 Out-of-Scope by Design
- Adaptive memory / vector retrieval
- repo convention learning
- memory quality scoring
- strategy shadow mode
- 고급 manual control (`skip node`, `retry only this node`)
- multi-repo orchestration hub generalization beyond current execution model
- zero-downtime / HA

## Residual Risks Accepted at Phase 1 Exit
- workflow editor는 운영용 수준까지 올라왔지만 고급 JSON patch/diff, side-by-side preview는 아직 없다.
- control-flow는 제한된 형태(`if_label_match`, `loop_until_pass`)만 지원한다.
- strategy engine은 rule-based이며 adaptive memory를 아직 쓰지 않는다.
- dashboard detail filtering은 더 고도화 여지가 있다.

## Required Regression Suite at Exit
- `PYTHONPATH=. .venv/bin/pytest -q`
- Expected baseline at exit:
  - all tests passing
  - product definition gates tested
  - improvement strategy tested
  - workflow control-flow tested
  - dashboard/runtime signal tests passing

## Phase 1 Deliverable Summary
- Product-definition artifacts and contracts
- Review / improvement / maturity / trend artifacts
- Workflow storage/editor/selector
- Workflow diff/preview
- Node-level execution history
- Resume / recovery / manual retry
- Edge-driven control-flow execution

## Exit Recommendation
- Phase 1은 종료 처리한다.
- 다음 단계는 `Phase 2: Adaptive Memory Layer`로 정의한다.
- Phase 2는 엔진 재작성보다 `memory/retrieval/convention/scoring` 계층을 현재 엔진 위에 점진적으로 추가하는 방향으로 진행한다.
