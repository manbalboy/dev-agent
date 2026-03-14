# Goal Closure Priority Reset

기준 시각: 2026-03-14 (KST)

## 1. 목적

- 이 문서는 `기능은 많이 됐는데, 진짜 목표에 도달하려면 무엇을 먼저 닫아야 하는가`를 다시 정리하는 우선순위 문서다.
- 기존 `PHASE3~PHASE8` 문서를 대체하지 않는다.
- 대신 상위 목표인 `안정적인 self-growing 개발 동료`를 기준으로, 남은 과제를 `핵심 필수 / 중기 필수 / 후순위`로 재배치한다.

## 2. 현재 전제

- 현재 상태는 `강한 기반을 가진 고급 프로토타입`이다.
- 기능형 제품으로서는 거의 완성권이지만, 목표 상태인 `믿고 오래 돌릴 수 있는 self-growing companion`에는 아직 못 미친다.
- 부족분의 본체는 `핵심 엔진의 마지막 폐쇄`와 `엔터프라이즈 운영 계층`이다.

## 3. 우선순위 재정렬

### 3.1 핵심 필수

1. `self-learning loop`를 `partial -> strong`으로 올리기
   - follow-up 효과를 장기 기간에서 누적 측정
   - recurring failure cluster의 재발 감소를 지속적으로 검증
   - regression / insufficient baseline을 다음 전략 전환에 실제로 반영

2. `AI fallback`을 `partial -> strong`으로 올리기
   - 한 모델/route가 이상할 때 다른 route가 안정적으로 이어받기
   - planner / reviewer / summary / escalation / test interpretation 경로의 fallback을 운영 기준으로 정리
   - provider health와 route fallback 상태를 operator가 바로 읽을 수 있게 만들기

3. `nonlinear runtime / vector retrieval / graph-subgraph`를 `partial -> strong`으로 올리기
   - planner / recovery / diagnosis 흐름의 shadow/opt-in 비중을 줄이기
   - vector retrieval을 `memory_search` 밖의 핵심 경로로 확장
   - graph/subgraph decision path를 operator-visible baseline으로 승격

4. `integration / operator control`을 `partial -> strong`으로 올리기
   - 통합 등록 -> 승인 -> env 준비 -> 가이드 주입 -> 사용 이력 -> blocked reason
   - failed job에서 왜 막혔는지 operator가 바로 이해하고 조치 가능해야 함
   - AI가 외부 서비스를 임의로 붙이지 못하고, 승인된 통합만 안전하게 사용

5. `앱 개발 모드 + emulator E2E`를 `baseline -> strong`으로 올리기
   - Android / iOS emulator 실행
   - 모바일 E2E 결과 surface
   - flaky / blocker / quality facet
   - 앱 타입별 품질 기준과 다음 fix action 연결

6. `엔터프라이즈 운영 계층 baseline` 닫기
   - patch status / patch progress 다음 단계
   - separate updater service
   - service drain / stop / restart
   - post-update health check
   - rollback baseline
   - backup / restore coupling

### 3.2 중기 필수

1. `Phase 8 enabling track`
   - `remaining runtime split / read-service long-tail`
   - `durable backend`
   - `self-check alert provider policy hardening`

2. `operator-facing graph/subgraph visualization baseline`
   - 현재 workflow 실행 경로와 graph/subgraph decision path 가시화
   - active branch / retry loop / fallback branch 비교

3. `self-growing 장기 증명`
   - 앱/클러스터 단위 장기 추세
   - operator action과 follow-up 효과의 상관관계
   - 재발 감소가 실제로 유지되는지 기간별 비교

### 3.3 후순위

1. `한/영 UI 완전 다국어`
2. `full visual editor / drag-and-drop` 급 graph workflow 편집기
3. `무중단 / HA`

## 4. Phase 7/8 관점에서의 재배치

### Phase 7 Focus

- 핵심 필수 항목을 닫는 구간
- 우선순위:
  1. patch/update 운영 계층
  2. AI fallback strong
  3. self-learning strong
  4. integration/operator control strong
  5. mobile emulator/E2E strong
  6. 남은 blocker는 Phase 8 enabling track으로 넘긴다.

### Phase 8 Focus

- 핵심 엔진 closure와 carry-over blocker를 같이 닫는 구간
- 우선순위:
  1. `Phase 8 enabling track`
  2. 비선형 runtime / graph-subgraph promotion
  3. vector retrieval promotion
  4. self-growing strong closure
  5. operator-facing graph/subgraph visualization baseline

### Phase 9 Focus

- 엔진 closure 이후 HA/무중단만 다루는 구간
- 우선순위:
  1. multi-worker claim model
  2. external queue / lock
  3. rolling restart / health-based traffic shift
  4. HA/무중단 운영 검증

## 5. 즉시 실행 우선순위

1. `Phase 8 enabling track`
   - `remaining runtime split / read-service long-tail`
   - `durable backend`
   - `self-check alert provider policy hardening`
2. `8-A Nonlinear Runtime Promotion`
   - planner / recovery / diagnosis 중 최소 하나를 primary-candidate로 승격
3. `8-B Vector Retrieval Promotion`
   - planner / reviewer retrieval rollout
   - hybrid metadata/vector comparison
4. `8-C Self-Growing Strong Closure`
   - recurring failure cluster 감소율을 실제 다음 전략 전환과 연결
5. `8-D Graph / Subgraph Visibility Baseline`
   - operator-facing graph/subgraph decision path surface

## 6. 종료 기준

- 아래가 되기 전에는 `진짜 목표에 도달했다`고 보지 않는다.
- self-growing follow-up이 장기적으로 품질 하락을 줄인다는 근거가 있다.
- 주요 AI route가 모델 이상/쿼터/인증 실패 시 다른 route로 이어진다.
- 비선형 runtime / vector retrieval / graph-subgraph가 shadow-only가 아니다.
- 통합/입력/승인 경계가 운영자 화면에서 명확하고, AI가 이를 존중한다.
- 앱 개발 시 emulator E2E 결과가 실제 다음 fix/review 흐름에 연결된다.
- 패치/재기동/복구가 운영 UI와 별도 updater 계층으로 관리된다.
- Phase 8 enabling track이 닫힌 뒤에만 Phase 9 HA로 넘어간다.

## 7. 한 줄 결론

- 지금부터의 우선순위는 `큰 파일 줄이기` 자체가 아니다.
- 지금부터의 우선순위는 `핵심 엔진을 strong으로 닫고, 엔터프라이즈 운영 계층을 올리는 것`이다.
