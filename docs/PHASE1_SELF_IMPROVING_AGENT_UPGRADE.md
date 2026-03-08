# Phase 1: Self-Improving Product Agent Upgrade

상위 운영 원칙은 [AI_AGENT_OPERATING_PRINCIPLES.md](./AI_AGENT_OPERATING_PRINCIPLES.md)를 따른다.

## 1) 현재 구조 문제점
- 현재 기본 흐름은 `issue -> spec -> plan -> implement -> review` 중심으로, 제품 정의 단계가 약함.
- `PRODUCT_BRIEF`, `USER_FLOWS`, `MVP_SCOPE`, `ARCHITECTURE_PLAN` 같은 제품 산출물이 기본 계약에 없었음.
- 리뷰 결과가 다음 작업으로 구조화되어 자동 전달되지 않아 반복 개선 연결성이 낮았음.
- 무한 루프 방지 장치가 테스트 루프에는 존재하지만 제품 품질 루프에는 부족했음.

## 2) 1차 개선 목표
- 제품 개발형 파이프라인 뼈대 도입
- 품질 평가 체계(Product Review) 도입
- 반복 개선 루프 데이터 구조 도입
- 2차 고도화를 위한 계약/산출물 표준화

## 3) 새 파이프라인 설계
- idea input
- read issue / write spec
- idea_to_product_brief
- generate_user_flows
- define_mvp_scope
- architecture_planning
- project_scaffolding
- planning / implementation / testing
- review_with_gemini
- product_review
- improvement_stage
- refine loop and finalize

## 3-1) AI 역할군 전략
- `Gemini`: planner / reviewer 계열 주 담당
- `Codex`: coder / designer / publisher / fix 계열 주 담당
- `Claude` / `Copilot`: documentation / escalation / helper 계열 보조 담당
- 실제 매핑은 `config/ai_role_routing.json`에서 관리하고, 오케스트레이터는 논리 역할명으로만 템플릿을 조회한다.

## 4) 단계별 계약
상세 계약은 `_docs/STAGE_CONTRACTS.md`, `_docs/STAGE_CONTRACTS.json` 자동 생성 파일을 표준으로 사용한다.

요약 계약:
- `idea_to_product_brief`: 입력(SPEC) -> 출력(PRODUCT_BRIEF.md)
- `generate_user_flows`: 입력(BRIEF) -> 출력(USER_FLOWS.md)
- `define_mvp_scope`: 입력(BRIEF/FLOWS/SPEC) -> 출력(MVP_SCOPE.md)
- `architecture_planning`: 입력(MVP/FLOWS) -> 출력(ARCHITECTURE_PLAN.md)
- `project_scaffolding`: 입력(ARCH/MVP/SPEC/repo context) -> 출력(SCAFFOLD_PLAN.md + BOOTSTRAP_REPORT.json)
- `product_review`: 입력(REVIEW/TEST/UX) -> 출력(PRODUCT_REVIEW.json + backlog/history)
- `improvement_stage`: 입력(product_review/history/backlog) -> 출력(loop_state/plan)

## 5) 새 산출물 파일 설계
- `_docs/PRODUCT_BRIEF.md`
- `_docs/USER_FLOWS.md`
- `_docs/MVP_SCOPE.md`
- `_docs/ARCHITECTURE_PLAN.md`
- `_docs/SCAFFOLD_PLAN.md`
- `_docs/BOOTSTRAP_REPORT.json`
- `_docs/PRODUCT_REVIEW.json`
- `_docs/REVIEW_HISTORY.json`
- `_docs/IMPROVEMENT_BACKLOG.json`
- `_docs/IMPROVEMENT_LOOP_STATE.json`
- `_docs/IMPROVEMENT_PLAN.md`
- `_docs/NEXT_IMPROVEMENT_TASKS.json`
- `_docs/STAGE_CONTRACTS.md`
- `_docs/STAGE_CONTRACTS.json`
- `_docs/PIPELINE_ANALYSIS.md`
- `_docs/PIPELINE_ANALYSIS.json`

## 6) PRODUCT_REVIEW.json 스키마
- 제안 스키마: `docs/schemas/PRODUCT_REVIEW.schema.json`
- 필수 평가 축:
  - code_quality
  - architecture_structure
  - maintainability
  - usability
  - ux_clarity
  - test_coverage
  - error_state_handling
  - empty_state_handling
  - loading_state_handling

## 7) 반복 개선 루프 설계
- `PRODUCT_REVIEW.json` 생성
- `REVIEW_HISTORY.json`에 점수 이력 누적
- `IMPROVEMENT_BACKLOG.json` 우선순위 항목 생성
- `IMPROVEMENT_PLAN.md`로 다음 루프 전략 생성
- `NEXT_IMPROVEMENT_TASKS.json`로 다음 루프 실행 가능한 작업 목록 생성

## 8) 무한 루프 방지 설계
- 같은 문제 반복 제한: 최근 top issue 반복 감지
- 품질 점수 정체 감지: 최근 3회 점수 변동폭 임계치 확인
- 품질 하락 감지: 직전 대비 하락 감지
- 이전 상태 복구 고려: 현재 git head 저장
- 전략 변경 조건: 위 감지 신호 중 하나라도 true면 stabilization 전략 적용

## 9) 변경 파일 목록
- `app/models.py`
- `app/workflow_design.py`
- `config/workflows.json`
- `app/orchestrator.py`
- `app/prompt_builder.py`
- `docs/schemas/PRODUCT_REVIEW.schema.json`
- `docs/PHASE1_SELF_IMPROVING_AGENT_UPGRADE.md`

## 10) 2차 개선 권장안
1. `product_review`를 LLM + 정적 분석 + 테스트 로그 기반 하이브리드 평가로 고도화
2. 개선 백로그를 실제 워크플로우 노드(`improvement_executor`)로 자동 실행
3. 단계별 품질 게이트를 정책 파일(`quality_policy.json`)로 외부화
4. loop guard를 job 단위뿐 아니라 repo 장기 히스토리 단위로 확장
