# Mobile App Development Mode Ruleset

기준 시각: 2026-03-13 (KST)

## 1. Purpose

- 이 문서는 `app` 분류 작업을 웹 작업과 다르게 다루기 위한 모바일 앱 개발 모드 기준이다.
- 목표는 두 가지다.
  - React Native 기반 앱 작업이 웹 규칙으로 흐트러지지 않게 한다.
  - 에뮬레이터/시뮬레이터 실행, 테스트, 운영자 입력(API key 등)을 작은 단위로 안전하게 연결한다.

## 2. Scope

- 적용 대상:
  - `PLAN.md` 의 Technology ruleset에서 `platform=app`
  - 이슈/스펙/코드 구조상 React Native/Expo/iOS/Android 앱으로 해석되는 작업
- 비적용 대상:
  - 일반 웹 프론트엔드
  - FastAPI/API 백엔드만 있는 저장소
  - Electron/Tauri 등 데스크톱 앱

## 3. Non-Negotiable Rules

### 3.1 Stack Rule
- `app` 분류 작업은 기본적으로 React Native 기준으로 계획하고 구현한다.
- greenfield 앱이면 `Expo managed workflow`를 우선 고려한다.
- 기존 저장소가 bare React Native / Expo prebuild / custom native 구조라면 기존 구조를 우선 보존한다.
- 웹 전용 라이브러리/패턴으로 모바일 앱 문제를 억지로 푸는 것은 금지한다.

### 3.2 Emulator Rule
- 한 라운드에 모든 플랫폼을 동시에 검증하려 하지 않는다.
- 기본 검증 타깃은 아래 우선순위를 따른다.
  1. 기존 저장소가 이미 쓰는 플랫폼
  2. Android emulator
  3. iOS simulator
- 이번 라운드에서 어떤 타깃을 검증 대상으로 삼는지 `PLAN.md`와 결과 문서에 남긴다.
- 실제 실행을 못 했으면 “미실행”으로 숨기지 말고, 어떤 명령에서 막혔는지 기록한다.

### 3.3 Test Rule
- 모바일 앱 baseline 테스트는 아래를 우선한다.
  - unit/component: `Jest` + `React Native Testing Library`
  - smoke/manual: emulator 또는 simulator에서 핵심 흐름 1개
- `Detox`/풀 E2E는 아래 조건일 때만 바로 요구한다.
  - 저장소에 이미 Detox가 있음
  - 또는 MVP 안정화 이후 품질 하드닝 라운드
- 앱 라운드에서도 아래 4상태를 반드시 점검한다.
  - loading
  - empty
  - error
  - offline/network failure

### 3.4 UI Rule
- 모바일 앱은 웹 대시보드형 밀도를 그대로 가져오지 않는다.
- 작은 화면 기준:
  - Android: `360 x 800` 전후
  - iPhone: `390 x 844` 전후
- 아래는 항상 확인한다.
  - safe area
  - keyboard overlap
  - bottom CTA reachability
  - 긴 텍스트 줄바꿈
  - 스크롤 끊김 여부

### 3.5 Secret / API Key Rule
- Google Maps, Firebase, Supabase, Stripe 같은 모바일 앱 입력은 하드코딩 금지.
- 필요 입력은 runtime input registry로 요청하고, 값은 env/config bridge로만 연결한다.
- prompt / 로그 / 문서에는 secret 값 자체를 쓰지 않는다.
- `필요한 키가 없어서 이번 라운드에서 막힌 기능`은 PLAN/REVIEW에 명시한다.

## 4. Required Planning Items

앱 분류 작업의 `PLAN.md`에는 최소 아래가 들어가야 한다.

1. 플랫폼 결정
   - Expo managed / bare RN / 기존 구조 유지 중 무엇인지
2. 이번 라운드 검증 타깃
   - Android emulator / iOS simulator / manual only
3. 앱 실행 명령
   - 예: `npx expo start --android`
   - 예: `npm run android`
4. 테스트 전략
   - unit/component 테스트
   - 이번 라운드 manual/emulator 확인 범위
5. 운영자 입력 필요 여부
   - 예: `GOOGLE_MAPS_API_KEY`, `EXPO_PUBLIC_API_BASE_URL`

## 5. Required Implementation Items

앱 분류 구현에서는 가능하면 아래를 같이 맞춘다.

- `package.json` 또는 동등 실행 계약에 아래 중 적어도 일부 존재
  - `start`
  - `android` 또는 Expo Android 실행 경로
  - `ios` 또는 Expo iOS 실행 경로
  - `test`
- screen/component 변경 시 테스트 가능 포인트 최소 1개 추가
- 네트워크 의존 화면이면 mock/fallback path 최소 1개 보강

## 6. Review Checklist

리뷰 단계에서는 아래 질문을 반드시 본다.

- 이번 변경이 실제로 모바일 앱 구조에 맞는가
- web 전용 가정이 섞이지 않았는가
- 에뮬레이터/시뮬레이터 검증 타깃이 명확한가
- safe area / keyboard / loading / empty / error / offline 처리가 있는가
- 플랫폼 키/secret이 하드코딩되지 않았는가
- 테스트가 전혀 없는데 UI만 커지지 않았는가

## 7. Small Slice Adoption Plan

### 7-A. Rule Injection
- planner/coder/reviewer prompt에 모바일 앱 규칙을 삽입
- 현재 상태: `DONE`

### 7-B. Workspace Runner Contract
- `workspace_app.sh` 또는 동등 스크립트에 RN/Expo app 실행 프리셋 추가
- 예:
  - Expo Android
  - Expo iOS
  - bare RN Android
  - bare RN iOS
- 현재 상태: `DONE`

### 7-C. Emulator Health Surface
- dashboard에서 현재 앱 저장소의 mobile run command / simulator target / last verification result 노출
- 현재 상태: `DONE`

### 7-D. Mobile Quality Artifact
- `_docs/MOBILE_APP_CHECKLIST.md` 또는 `_docs/UI_BENCHMARK_NOTES.md`에 모바일 점검 결과 기록
- 현재 상태: `DONE`

### 7-E. Emulator Boot + Mobile E2E Runner
- `scripts/mobile_e2e_runner.sh`가 Android emulator / iOS simulator를 부팅 또는 재사용하고 E2E 명령을 실행
- `scripts/run_agenthub_tests.sh`는 아래 모드를 지원
  - `mobile-e2e-android`
  - `mobile-e2e-ios`
  - `e2e` 모드에서 mobile E2E script가 있으면 Android 우선으로 자동 선택
- 결과는 `_docs/MOBILE_E2E_RESULT.json`에 기록되고 `_docs/MOBILE_APP_CHECKLIST.md`에도 같이 요약됨
- 현재 상태: `DONE`

### 7-F. Mobile E2E Operator Surface
- job detail의 workflow 탭에서 마지막 `MOBILE_E2E_RESULT`를 직접 본다.
- admin 운영 지표의 `앱 실행 상태` 카드에서 최근 모바일 E2E 결과와 상태 분포를 같이 본다.
- 현재 상태: `DONE`

## 8. Exit Criteria

- planner/coder/reviewer가 앱 분류 작업에서 React Native/Expo/에뮬레이터 규칙을 기본 전제로 다룬다.
- 모바일 앱 이슈에서 web 전용 해결책이 기본안으로 나오지 않는다.
- 필요한 API key는 runtime input registry 경유로 요청할 수 있다.
- dashboard 운영 지표에서 앱별 실행 모드와 최근 실행 명령을 읽을 수 있다.
- 앱 작업의 테스트 단계가 끝나면 `_docs/MOBILE_APP_CHECKLIST.md`에 마지막 emulator/simulator 검증 요약이 자동 기록된다.
- mobile E2E를 실행하면 `_docs/MOBILE_E2E_RESULT.json`에 platform / target / runner / command / status가 기록된다.
- job detail과 admin 운영 지표에서 마지막 모바일 E2E 결과를 직접 읽을 수 있다.
