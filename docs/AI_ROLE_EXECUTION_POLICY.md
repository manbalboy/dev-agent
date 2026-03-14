# AI Role Execution Policy

기준 시각: 2026-03-13 (KST)

이 문서는 `Gemini / Codex / bash`의 역할 경계를 명확하게 고정하기 위한 운영 기준이다.

## 1. 목표

- 구현과 판단 역할을 섞지 않는다.
- 테스트 실행과 테스트 해석을 분리한다.
- commit / PR / escalation 같은 운영 메시지는 구현 모델이 아니라 판단 모델이 맡는다.
- 역할 변경은 파이썬 코드 하드코딩이 아니라 `config/ai_role_routing.json`, `config/roles.json`, `config/ai_commands*.json`으로 제어한다.

## 2. 기본 역할 분리

### Codex

- 구현
- 리팩터
- 퍼블리싱
- 카피라이팅 산출물 작성
- 기술 문서 실제 작성
- 보조 오케스트레이션/로그 분석

주요 role / route:

- `coder`
- `designer`
- `publisher`
- `copywriter`
- `documentation -> tech-writer`
- `codex_helper`

### Gemini

- 제품/전략 관점 계획
- 코드 리뷰
- 테스트 결과 해석
- 품질 게이트 판단
- commit 제목/요약
- PR 본문 요약
- escalation 요약

주요 role / route:

- `planner -> architect`
- `reviewer -> reviewer`
- `commit_summary -> summary-reviewer`
- `pr_summary -> summary-reviewer`
- `escalation -> summary-reviewer`
- `test_reviewer -> test-reviewer`

### bash / shell

- pytest / npm test / Playwright / emulator / simulator 실행
- Git / system 명령 실행

주요 role:

- `tester`
- `qa`
- `test-automation`
- `devops-sre`

## 3. 테스트 정책

- 실제 테스트 실행은 계속 `bash`가 맡는다.
- 테스트 리포트 해석과 품질 게이트 판단은 `Gemini`가 맡는다.
- 현재 baseline route는 `test_reviewer`이고, 구현상 기존 `reviewer` 경로와 같은 Gemini 템플릿을 재사용한다.
- 즉, `실행 = bash`, `판정 = Gemini`를 기본 계약으로 둔다.

## 4. 문서/요약 정책

- `documentation` route는 `tech-writer` / `Codex`를 유지한다.
- `commit_summary`, `pr_summary`, `escalation`은 `summary-reviewer` / `Gemini`로 돌린다.
- commit summary 생성은 먼저 Gemini route를 시도하고, 실패 시에만 Codex helper를 fallback으로 사용한다.

## 5. Fallback 원칙

- 1차 판단 모델은 `Gemini`
- 1차 구현 모델은 `Codex`
- 판단 route가 실패하면 Codex helper fallback은 허용한다.
- 다만 fallback은 예외 경로이고, 기본 policy는 바꾸지 않는다.

## 6. 구현 기준 파일

- 라우팅 정책:
  - [config/ai_role_routing.json](../config/ai_role_routing.json)
  - [app/ai_role_routing.py](../app/ai_role_routing.py)
- 역할 카탈로그:
  - [config/roles.json](../config/roles.json)
  - [app/dashboard_roles_runtime.py](../app/dashboard_roles_runtime.py)
- 명령 템플릿:
  - [config/ai_commands.json](../config/ai_commands.json)
  - [config/ai_commands.example.json](../config/ai_commands.example.json)
- commit / PR summary 실행:
  - [app/summary_runtime.py](../app/summary_runtime.py)

## 7. 현재 상태

- `documentation`: Codex
- `commit_summary`: Gemini primary, Codex helper fallback
- `pr_summary`: Gemini primary
- `escalation`: Gemini primary, Codex helper fallback
- `test_reviewer`: Gemini route baseline added
- `tester`: bash 유지

## 8. 다음 단계

- `test_reviewer` 전용 artifact / stage 연결
- dashboard에서 route별 provider 상태를 더 명확히 표시
- provider health card에 `Gemini judgement routes` / `Codex implementation routes` 분리 표기
