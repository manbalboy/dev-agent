# UI Quality And Operator Inputs Plan

## 1. Purpose
- 이 문서는 기능이 늘수록 UI 품질이 떨어지는 문제와, 작업 도중 나중에 받아야 하는 사용자 입력을 안전하게 처리하는 문제를 같이 다룬다.
- 목표는 두 가지다.
  - 기능 추가가 진행되어도 `모바일 우선`, `모던`, `심플`, `정보 위계 명확` 상태를 유지한다.
  - AI가 나중에 필요로 할 수 있는 API key / tenant id / base URL 같은 값을 운영자가 나중에 넣어도 workflow가 다시 활용할 수 있게 만든다.

## 2. Benchmark Set
- 이 문서는 정확한 시장 순위 문서가 아니라, 실제로 벤치마킹 가치가 높은 상위권 레퍼런스 제품군을 기준으로 삼는다.
- 기준 제품:
  - `Linear`: 빠른 issue/workflow UX, 요약 우선 정보 위계, 복잡도 통제
    - https://linear.app
  - `Vercel`: summary -> drilldown 구조, 운영 상태를 카드/리스트로 단순화
    - https://vercel.com
  - `Supabase`: 설정/운영/데이터 기능이 많아도 메뉴 분리와 화면 밀도 통제가 좋음
    - https://supabase.com
  - `Sentry`: 복잡한 진단 정보를 한 화면에 다 쏟지 않고 overview -> detail 흐름으로 푼다
    - https://sentry.io

## 3. Non-Negotiable Rules

### 3.1 UI Rule
- 새 기능이 들어와도 한 화면에 모든 것을 동시에 보여주지 않는다.
- 모바일 기준 폭 `360px ~ 430px` 에서 우선 레이아웃이 깨지지 않아야 한다.
- 새 카드/새 패널/새 표를 추가할 때는 반드시 아래 중 하나를 같이 한다.
  - 기존 정보를 접는다.
  - 탭/메뉴로 분리한다.
  - 요약/상세 구조로 쪼갠다.
- “기능 추가”보다 “복잡도 관리”를 우선한다.

### 3.2 Operator Input Rule
- secret 값은 기본적으로 prompt/log/UI에 평문 노출하지 않는다.
- secret 값은 저장은 하더라도 dashboard/API에서는 마스킹만 보여준다.
- prompt에는 값 자체가 아니라 `env var 존재 여부`, `요청/제공 상태`, `무엇이 막혀 있는지`만 노출한다.
- 실제 값 사용은 shell/test/runtime env bridge를 통해서만 허용한다.

### 3.3 Small Slice Rule
- 한 슬라이스는 아래를 넘기지 않는다.
  - 화면 변경 최대 1개
  - API 최대 1~2개
  - artifact 최대 1개
  - 런타임 연결 최대 1개

### 3.4 Test Rule
- 모든 슬라이스는 아래를 포함한다.
  - unit test
  - API contract/regression test
  - off-path test
  - full `PYTHONPATH=. .venv/bin/pytest -q`
- UI 변경은 가능하면 `node --check` 와 템플릿 script 문법 검사를 같이 한다.

## 4. Current Risks
- dashboard/admin 화면은 기능이 쌓이면서 카드/리스트/폼이 많아지고 있다.
- 같은 화면에서 overview와 operator action이 같이 커지면 모바일 레이아웃이 먼저 깨질 가능성이 높다.
- 앱 생성 대상 저장소도 비슷한 문제가 생길 수 있다.
  - 기능 추가가 계속 UI를 덧대는 방향으로 가면, 제품 품질이 시간이 갈수록 하락한다.
- 외부 서비스 의존 기능은 API key가 없으면 멈추는데, 지금까지는 이 요구사항을 runtime contract로 저장하는 구조가 약했다.

## 5. Work Packages

### 5-A. UI Quality Guardrails
- Goal:
  - planner/coder가 UI 작업을 할 때 품질 하락을 기본적으로 막는다.
- Small Slices:
  - `5-A1` planner prompt에 benchmark/mobile/complexity guardrail 추가
  - `5-A2` coder prompt에 mobile-first/simple-layout guardrail 추가
  - `5-A3` 각 app의 `_docs/UI_BENCHMARK_NOTES.md` 산출물 도입
- Current Status:
  - `PARTIAL`
- Done:
  - planner/coder prompt에 모바일 우선, complexity budget, benchmark direction, operator input 반영 규칙 추가
- Remaining:
  - benchmark note artifact 생성
  - app별 UI review checklist 연결

### 5-B. Dashboard Information Architecture Cleanup
- Goal:
  - dashboard가 기능을 추가할수록 더 복잡해지지 않게 한다.
- Small Slices:
  - `5-B1` overview와 action 영역을 분리
  - `5-B2` operator forms를 접힘/선택 상세 구조로 이동
  - `5-B3` mobile priority layout audit
- Rule:
  - 새 admin 기능은 가능하면 새 탭이 아니라 기존 admin panel 안의 독립 섹션으로 추가한다.
  - 단, 한 섹션이 너무 커지면 별도 tab으로 승격한다.

### 5-C. Operator Runtime Input Registry
- Goal:
  - AI가 나중에 필요로 할 수 있는 입력을 저장하고, 나중에 운영자가 제공할 수 있게 한다.
- Small Slices:
  - `5-C1` runtime input request/value store
  - `5-C2` admin dashboard request/provide UI
  - `5-C3` prompt-safe artifact + shell env bridge
  - `5-C4` job detail visibility
  - `5-C5` AI-suggested request draft behind operator approval
- Current Status:
  - `PARTIAL`
- Done:
  - runtime input store
  - admin list/request/provide API
  - admin panel request/provide UI
  - prompt-safe `_docs/OPERATOR_INPUTS.json`
  - shell/template env bridge
  - job detail read-only visibility
  - AI/템플릿 기반 runtime input draft recommendation API
  - operator approval 버튼을 통한 draft -> 실제 request 등록 경로
  - 기본 템플릿 라이브러리 첫 슬라이스 (`Google Maps`, `Stripe`, `Supabase`)
- Remaining:
  - template library 확장
  - app별 추천 템플릿 정밀도 보강

### 5-D. Secret Safety Hardening
- Goal:
  - 운영자 입력이 늘어도 secret 취급이 깨지지 않게 한다.
- Small Slices:
  - `5-D1` prompt artifact에서 secret 값 제거 검증
  - `5-D2` log redaction regression 추가
  - `5-D3` dead-letter/needs_human 이유에 required input 상태 연결

## 6. Immediate Next Steps
1. `_docs/UI_BENCHMARK_NOTES.md` 생성 슬라이스 추가
2. draft recommendation을 assistant diagnosis/job context와 더 직접 연결
3. runtime input template library를 app별로 확장
4. dashboard admin panel 모바일 레이아웃 점검

## 7. Exit Criteria
- planner/coder가 UI 작업 시 mobile-first / complexity control / benchmark direction을 기본적으로 고려한다.
- 운영자는 dashboard에서 runtime input request를 등록하고 나중에 값을 제공할 수 있다.
- secret 값은 prompt/log/UI에 평문 노출되지 않는다.
- runtime은 제공된 값을 env bridge로 사용할 수 있다.
- 다음 슬라이스 전까지 full regression이 유지된다.
