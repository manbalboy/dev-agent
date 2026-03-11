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
- `Claude` / `Copilot`: 문서화, 에스컬레이션, 보조 분석 같은 보조 역할
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
  - 상세 설계/도입 순서: [PHASE4_AGENTIC_RUNTIME_ADOPTION_PLAN.md](./PHASE4_AGENTIC_RUNTIME_ADOPTION_PLAN.md)

### Phase 5. Continuous Job Operations
- 목적: 장기 운영 시 stuck job / provider outage / worker restart에 견디게 만든다.
- 핵심 작업:
  - heartbeat / timeout / stuck detection
  - orphan `running` recovery
  - failure classification
  - retry policy split
- 완료 조건:
  - worker 재시작 후 running 고착 작업을 자동 정리/복구

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
- Sprint goal: `project_scaffolding`를 명시 단계로 추가하고 산출물/계약/테스트까지 연결한다.
- In scope:
  - stage enum 추가
  - workflow node 추가
  - orchestrator scaffold 단계 추가
  - `_docs/SCAFFOLD_PLAN.md` / `_docs/BOOTSTRAP_REPORT.json` 생성
  - stage contract / pipeline analysis 반영
  - 회귀 테스트 반영
- Out of scope:
  - improvement executor
  - running recovery
  - HA / 무중단

## Acceptance Criteria
- 새 job 실행 시 scaffold 단계가 stage order에 포함된다.
- scaffold 산출물이 레포 `_docs` 아래 생성된다.
- workflow-config path와 fixed pipeline path 모두 scaffold 단계를 인식한다.
- 테스트가 새 stage 존재와 산출물 생성을 검증한다.
