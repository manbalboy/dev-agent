# Phase 8 Nonlinear Engine And Self-Growing Plan

기준 시각: 2026-03-14 (KST)

## 1. 목적

- Phase 8의 목적은 이 시스템을 `기능이 많은 운영 도구`에서 `비선형적으로 조사하고, memory/tool/vector/graph를 재사용하며, 실제로 다음 전략을 바꾸는 self-growing engine`으로 끌어올리는 것이다.
- 이 phase는 기존 Phase 4를 다시 시작하는 단계가 아니다.
- 대신 이미 `PARTIAL / shadow / opt-in`으로 들어와 있는 tool/vector/graph/self-growing 요소를 `strong / operator-visible / primary-candidate` 상태까지 닫는 phase다.

## 2. 왜 지금 이 재배치가 필요한가

- Phase 7은 patch/update, backup/restore, hygiene, security/TLS, periodic self-check baseline을 거의 닫았다.
- 반면 핵심 엔진은 아직 아래 상태에 머물러 있다.
  - 비선형 workflow/runtime: 구조는 있으나 graph/subgraph promotion이 제한적
  - vector retrieval: `memory_search` 중심 opt-in 실험에 머묾
  - LangGraph: planner/recovery shadow trace 단계
  - self-growing: 효과 측정은 있으나 장기 전략 전환까지는 약함
- 이 상태에서 바로 `Phase 9 HA/무중단`으로 가면, 아직 덜 닫힌 엔진을 더 안정적으로 오래 돌리는 데 그칠 위험이 있다.
- 따라서 `Phase 8 = 엔진 closure`, `Phase 9 = HA`로 재배치하는 것이 더 맞다.

## 3. 재정의 원칙

- `Phase 4` 문서는 유지한다.
  - adoption / shadow / opt-in 도입 기록의 source-of-truth다.
- `Phase 8`은 `Phase 4 strong-closure phase`로 본다.
  - 즉 “없는 걸 새로 만든다”보다 “partial인 걸 strong으로 승격한다”에 가깝다.
- `Phase 7`의 남은 일은 별도 phase를 늘리지 않고 `Phase 8 enabling track`으로 편입한다.
- `Phase 9`는 `Zero-Downtime / HA` 전용 phase로 남긴다.

## 4. 현재 이미 있는 기반

### 4.1 비선형 실행 기반

- edge-driven workflow
- `if_label_match`
- `loop_until_pass`
- `node_runs`
- failed-safe resume / manual resume

즉, 선형만 있는 상태는 이미 아니다. 남은 일은 graph/subgraph를 더 명시적이고 reusable하게 승격하는 것이다.

### 4.2 vector / Qdrant 기반

- SQLite `memory_runtime.db` source-of-truth
- `_docs/VECTOR_SHADOW_INDEX.json` shadow candidate
- optional Qdrant shadow transport
- `memory_search` route opt-in vector retrieval
- planner / reviewer / coder `MEMORY_CONTEXT` opt-in vector retrieval baseline

즉, vector search도 “없음”이 아니라 “좁은 곳에만 있음” 상태다.

### 4.3 LangGraph / subgraph 기반

- planner shadow trace
- recovery shadow trace
- operator trace visibility baseline

즉, graph/subgraph도 “실험중인 shadow path”까지는 이미 들어왔다.

### 4.4 self-growing 기반

- follow-up backlog candidate
- approved backlog -> next job enqueue bridge
- recurring failure cluster ingest
- `_docs/SELF_GROWING_EFFECTIVENESS.json`
- 최근 7일 추세 / 앱별 효과 / regression / insufficient baseline facet

즉, 성장 루프도 “측정만 하는 상태”를 넘어 일부 연결은 이미 있다.

## 5. Phase 8 우선순위

### 8-A. Nonlinear Runtime Promotion

- 목표:
  - planner / recovery / diagnosis 흐름 중 최소 일부를 `shadow trace`에서 `primary-candidate` 수준으로 승격한다.
- 범위:
  - reusable graph/subgraph abstraction
  - state checkpoint / resume contract 정리
  - divergence trace와 fallback 조건 명시
  - operator가 graph decision path를 비교 가능하게 유지
- 완료 기준:
  - 적어도 하나 이상의 subgraph 경로가 shadow-only가 아니라 opt-in 또는 default-adjacent 상태가 된다.

### 8-B. Vector Retrieval Promotion

- 목표:
  - vector retrieval을 `memory_search only`에서 더 넓은 실사용 경로로 확장한다.
- 범위:
  - planner / reviewer / coder 후보 retrieval rollout
  - metadata + vector hybrid query hardening
  - shadow-vs-primary quality comparison
  - operator-readable retrieval source / score / fallback surface
- 완료 기준:
  - vector retrieval이 한정 실험이 아니라 핵심 engine path의 선택 가능한 retrieval backend가 된다.

### 8-C. Self-Growing Strong Closure

- 목표:
  - follow-up / failure cluster / regression signal이 실제 다음 전략을 바꾸게 한다.
- 범위:
  - 장기 기간 effectiveness 추적
  - recurring failure 감소율을 전략 전환과 연결
  - regression / insufficient baseline을 다음 planner/fixer 입력으로 실제 반영
  - operator action과 개선 효과의 상관관계 surface
- 완료 기준:
  - self-growing loop가 “artifact를 남김”이 아니라 “다음 행동을 바꿈” 상태가 된다.

### 8-D. Graph / Subgraph Visibility Baseline

- 목표:
  - operator가 현재 workflow와 graph/subgraph decision path를 “진짜 그래프처럼” 읽을 수 있는 가시성을 만든다.
- 범위:
  - node / edge / state overlay
  - active path / retry loop / fallback branch 표시
  - planner/recovery subgraph trace visualization
- 주의:
  - 이 단계는 `operator-facing visualization baseline`이다.
  - full visual editor / drag-and-drop builder는 이 단계의 목표가 아니다.
- 완료 기준:
  - 현재 workflow 실행과 graph/subgraph shadow/primary path를 UI에서 구조적으로 읽을 수 있다.

### 8-E. Phase 7 Carry-Over Enabling Track

- 목표:
  - 엔진 closure를 막는 남은 기반 리스크를 같이 닫는다.
- 이 track으로 넘기는 항목:
  - `remaining runtime split / read-service long-tail`
  - `durable backend` 경계 보강
  - `self-check alert provider policy hardening`
- 원칙:
  - 이 항목들은 `Phase 7을 계속 늘리는 이유`가 아니라 `Phase 8 엔진 승격을 위한 지원 트랙`으로 본다.

## 6. 실행 순서

1. `8-E` 중 blocker만 먼저 닫는다.
   - runtime split long-tail
   - durable backend 최소 경계
   - self-check alert provider policy
2. `8-A` Nonlinear Runtime Promotion
3. `8-B` Vector Retrieval Promotion
4. `8-C` Self-Growing Strong Closure
5. `8-D` Graph / Subgraph Visibility Baseline

주의:
- 시각화는 중요하지만, graph runtime이 약한 상태에서 UI만 먼저 키우면 hollow feature가 된다.
- 따라서 시각화는 `엔진 승격 이후 같은 Phase 8 안에서` 붙인다.

## 7. Phase 9로 넘어가기 전 종료 기준

- 아래가 모두 닫혀야만 `Phase 9`로 넘어간다.

1. `8-E` carry-over 항목이 닫혀 있다.
   - runtime split long-tail
   - durable backend 경계
   - self-check alert provider policy
2. 비선형 runtime이 shadow-only가 아니다.
   - planner/recovery/diagnosis 중 최소 하나는 primary-candidate로 승격
3. vector retrieval이 `memory_search` 밖의 핵심 path로 올라왔다.
4. self-growing loop가 장기 효과를 근거로 실제 다음 전략을 바꾼다.
5. operator-facing graph/subgraph visualization baseline이 있다.

## 8. Phase 9 정의

- Phase 9는 `Zero-Downtime / HA` 전용 phase다.
- 여기에는 아래만 넣는다.
  - multi-worker claim model
  - external queue / lock
  - rolling restart / health-based traffic shift
  - 무중단/HA 운영 검증
- 즉, `엔진이 덜 닫힌 상태에서 HA를 먼저 하지 않는다`가 이번 재정의의 핵심이다.

## 9. 이번 재배치의 한 줄 결론

- `Phase 7`은 baseline/hardening 마무리 구간으로 닫는다.
- `Phase 8`은 비선형성 / vector / graph/subgraph / self-growing을 strong으로 닫는 핵심 엔진 phase로 올린다.
- `Phase 9`는 그 뒤에 오는 HA/무중단 phase로 미룬다.
