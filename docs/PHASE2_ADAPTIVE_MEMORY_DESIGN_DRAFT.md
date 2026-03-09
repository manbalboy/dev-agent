# Phase 2 Adaptive Memory Design Draft

## 1. Intent
- Phase 2의 목표는 현재의 `rule-based orchestrator + LLM workers` 구조를 버리지 않고, 그 위에 `adaptive memory layer`를 얹어 과거 실패를 학습하고 repo convention을 흡수하는 자기 적응형 에이전트로 고도화하는 것이다.
- 핵심은 `vector DB 도입` 자체가 아니라, `무엇을 기억하고`, `언제 검색하고`, `어떻게 검증하고`, `언제 폐기할지`를 운영 가능한 구조로 만드는 것이다.

## 2. Non-Goals
- 오케스트레이터 전면 재작성
- LLM이 다음 노드를 자유롭게 선택하는 구조
- hard gate / loop guard를 memory가 대체하는 구조
- zero-downtime / HA
- 일반 목적 멀티-tenant knowledge graph

## 3. Phase 2 Design Principle
- 기존 spine은 유지한다.
  - issue -> spec -> product definition -> architecture -> scaffold -> plan -> implement -> review -> improvement
- 새 기능은 옆에 붙인다.
  - memory writer
  - memory retriever
  - convention extractor
  - memory scorer
- 모든 adaptive 기능은 `shadow -> opt-in -> partial default -> full default` 순서로 도입한다.
- memory는 source of truth가 아니라 retrieval aid다.
- 최종 정책 결정은 계속 rule / quality gate / trend / loop guard가 맡는다.

## 4. Phase 2 Main Theme
- `Self-Evolving Memory Layer`
- 목표:
  - 같은 실패 반복 감소
  - repo convention 위반 감소
  - strategy precision 향상
  - 불필요한 full rerun 감소
  - 프로젝트별 개인화된 계획/리뷰/수정 흐름 축적

## 5. Architecture Overview

### 5.1 Current Base (Preserved)
- orchestrator
- workflow engine
- node_runs
- review / improvement loop
- hard gate
- repo maturity / quality trend

### 5.2 New Memory Layer (Additive)
- `memory_writer`
- `memory_retriever`
- `convention_extractor`
- `memory_scorer`
- `prompt_memory_injector`

## 6. Memory Types

### 6.1 Episodic Memory
- 목적: 특정 job/attempt/loop에서 무슨 일이 있었는지 기억
- 예:
  - 어떤 전략이 먹혔는가
  - 어느 node에서 자주 실패했는가
  - 어떤 수정이 regression을 냈는가
- Example fields:
  - job_id
  - app_code
  - workflow_id
  - issue_signature
  - failing_node_type
  - chosen_strategy
  - result_delta
  - recovery_mode

### 6.2 Convention Memory
- 목적: repo의 스타일/구조/운영 규칙 기억
- 예:
  - 파일 배치 규칙
  - 상태관리 방식
  - 테스트 위치
  - naming 규칙
  - API router 구조
- Example fields:
  - repo
  - app_code
  - convention_type
  - rule
  - evidence_paths
  - confidence

### 6.3 Decision Memory
- 목적: 왜 특정 전략/분기를 선택했는지 남김
- 예:
  - 왜 `test_hardening`을 골랐는가
  - 왜 `feature_expansion`을 막았는가
- Example fields:
  - trigger_signals
  - chosen_strategy
  - rejected_alternatives
  - reason

### 6.4 Failure Pattern Memory
- 목적: 반복되는 실패 패턴과 대응법 축적
- 예:
  - 특정 test gate 실패 후 어떤 수정을 우선해야 하는가
  - 특정 상태 처리 카테고리가 지속 저점일 때 어떤 branch를 타야 하는가
- Example fields:
  - failure_signature
  - matched_signals
  - recommended_action
  - historical_success_rate

## 7. Phase 2 Incremental Delivery

### Phase 2-A. Structured Memory MVP
- 목표:
  - vector DB 없이 memory schema를 먼저 만든다.
  - 현재 JSON 산출물을 memory entry로 정규화한다.
- Deliverables:
  - `MEMORY_LOG.jsonl`
  - `DECISION_HISTORY.json`
  - `FAILURE_PATTERNS.json`
  - `CONVENTIONS.json`
- Scope:
  - write-only 우선
  - retrieval 없음 또는 단순 규칙 조회만
- Success criteria:
  - 각 job 종료 후 최소 1개 episodic memory와 1개 decision memory가 저장된다.

### Phase 2-B. Controlled Retrieval
- 목표:
  - planner / reviewer / fixer 전에 유사 memory를 회수해 prompt에 주입한다.
- Insert points:
  - planner 전
  - reviewer 전
  - coder/fix 전
- Retrieval rules:
  - same repo/app 우선
  - recent first
  - similar failure/category 우선
  - top-k 제한
  - confidence threshold 적용
- Deliverables:
  - `MEMORY_CONTEXT.json`
  - `MEMORY_SELECTION.json`
- Success criteria:
  - retrieval 결과가 prompt에 구조화되어 들어가고, 길이 상한을 넘지 않는다.

### Phase 2-C. Convention Extraction
- 목표:
  - repo 성공 라운드와 안정 코드에서 convention을 추출한다.
- Sources:
  - 성공한 PR diff
  - stable paths
  - 테스트 파일
  - 설정 파일
  - review 결과
- Deliverables:
  - `CONVENTIONS.md`
  - `CONVENTIONS.json`
  - `ARCHITECTURE_RULES.json`
- Success criteria:
  - coder/reviewer가 convention summary를 직접 참고한다.

### Phase 2-D. Memory Quality Scoring
- 목표:
  - 저장된 memory를 승격/감쇠/보관/차단한다.
- Rules:
  - memory 사용 후 점수 상승 -> promote
  - memory 사용 후 regression -> decay or ban
  - 반복적으로 유효 -> confidence 상승
- Deliverables:
  - `MEMORY_FEEDBACK.json`
  - `MEMORY_RANKINGS.json`
- Success criteria:
  - 도움이 된 memory와 오염된 memory가 분리 관리된다.

### Phase 2-E. Adaptive Strategy Shadow Mode
- 목표:
  - 기존 strategy engine을 유지한 상태에서 memory-augmented strategy를 shadow로 비교한다.
- Rules:
  - 실행은 기존 strategy 유지
  - memory-aware strategy는 로그로만 기록
  - 충분한 비교 데이터가 쌓인 뒤 opt-in rollout
- Deliverables:
  - `STRATEGY_SHADOW_REPORT.json`
- Success criteria:
  - new strategy precision을 실제 실행 영향 없이 비교 가능

## 8. Data Model Draft

### 8.1 MEMORY_LOG.jsonl
```json
{"memory_type":"episodic","job_id":"...","app_code":"...","workflow_id":"...","issue_signature":"...","signals":{"strategy":"test_hardening","quality_delta":0.4},"outcome":{"success":true}}
```

### 8.2 CONVENTIONS.json
```json
{
  "repo": "owner/repo",
  "app_code": "food-random",
  "rules": [
    {
      "id": "conv_component_path",
      "type": "filesystem",
      "rule": "UI components live under app/components",
      "evidence_paths": ["app/components/Button.tsx"],
      "confidence": 0.82
    }
  ]
}
```

### 8.3 MEMORY_SELECTION.json
```json
{
  "job_id": "...",
  "planner_context": ["mem_001", "mem_014"],
  "reviewer_context": ["mem_022"],
  "coder_context": ["mem_014", "conv_003"]
}
```

## 9. Vector DB Positioning
- vector DB는 Phase 2-B 이후의 `retrieval backend`로 도입한다.
- 역할:
  - 유사 과거 작업 찾기
  - 유사 실패 패턴 찾기
  - 유사 convention 후보 찾기
- 하지 말아야 할 역할:
  - source of truth
  - final policy engine
  - hard gate replacement
  - loop guard replacement

## 10. Runtime Integration Points

### 10.1 Planner
- 입력:
  - PRODUCT_BRIEF / USER_FLOWS / MVP_SCOPE / ARCHITECTURE_PLAN
  - IMPROVEMENT_LOOP_STATE / NEXT_IMPROVEMENT_TASKS
  - MEMORY_CONTEXT.planner
- 기대 효과:
  - 과거 유사 성공 설계를 빠르게 재사용

### 10.2 Reviewer
- 입력:
  - PRODUCT_REVIEW evidence
  - CONVENTIONS
  - MEMORY_CONTEXT.reviewer
- 기대 효과:
  - repo 맞춤형 품질 평가

### 10.3 Coder / Fixer
- 입력:
  - PLAN / REVIEW / IMPROVEMENT artifacts
  - CONVENTIONS
  - MEMORY_CONTEXT.coder
- 기대 효과:
  - 반복되는 repo 스타일 위반 감소

## 11. Risk Controls
- 모든 memory feature는 flag 뒤에 둔다.
- 기본값은 `off` 또는 `shadow`다.
- 기존 strategy / hard gate / recovery를 유지한다.
- memory retrieval은 길이 상한과 confidence threshold를 강제한다.
- memory poisoning 방지를 위해 scoring 없이는 full autonomy를 허용하지 않는다.

## 12. Suggested Feature Flags
- `enable_memory_logging`
- `enable_memory_retrieval_shadow`
- `enable_memory_retrieval_planner`
- `enable_memory_retrieval_reviewer`
- `enable_memory_retrieval_coder`
- `enable_convention_learning`
- `enable_memory_scoring`
- `enable_strategy_v2_shadow`

## 13. MVP / Out-of-Scope

### MVP for Phase 2
- structured memory write
- planner/reviewer/coder retrieval injection
- convention extraction v1
- memory scoring v1
- shadow strategy comparison

### Out-of-Scope for Phase 2 MVP
- fully autonomous branch selection by LLM
- memory-only policy enforcement
- online learning without review
- multi-tenant knowledge service

## 14. Success Metrics
- repeated failure rate 감소
- convention violation 감소
- unnecessary full rerun 감소
- strategy precision 향상
- review score improvement velocity 상승

## 15. Recommended Implementation Order
1. Structured memory schema and write path
2. Convention extractor
3. Retrieval shadow mode
4. Prompt injection for planner/reviewer/coder
5. Memory scoring
6. Strategy shadow mode
7. Opt-in adaptive workflow rollout

## 16. Rollout Strategy
- Stage 1: write-only
- Stage 2: retrieval shadow
- Stage 3: planner-only opt-in
- Stage 4: reviewer/coder opt-in
- Stage 5: strategy shadow comparison
- Stage 6: selective default enablement

## 17. Phase 2 Exit Criteria Draft
- memory entries are persisted for every completed job
- planner/reviewer/coder can consume bounded memory context
- convention extraction produces reusable structured rules
- memory scoring promotes/decays entries
- strategy shadow mode produces measurable comparison data
- no regression in existing Phase 1 hard gates, resume, or recovery

## 18. Recommendation
- Phase 2는 `memory + retrieval + convention + scoring`을 중심으로 시작한다.
- vector DB는 retrieval backend로만 제한적으로 도입한다.
- 현재 orchestrator와 workflow engine은 유지하고, adaptive layer를 점진적으로 올리는 방식으로 진행한다.
