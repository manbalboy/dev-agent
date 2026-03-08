# Workflow Node 전환 2차 설계 문서

## 1. 목적
1차에서는 워크플로우 스키마, 저장소, 검증 API를 도입했다.

현재 코드는 1차 문서 작성 당시 예상보다 더 진행되어 있다.
- 기본 워크플로우 JSON 로딩
- 기본 워크플로우 검증 후 실행
- 로딩 실패 시 고정 파이프라인 fallback
- 일부 노드 타입의 실제 dispatch 실행

2차의 목표는 "노드 정의를 저장한다"에서 끝내지 않고, "선택된 워크플로우를 안정적으로 실행하고 추적한다"까지 올리는 것이다.

---

## 2. 1차 문서에서 정의했던 2차 범위
`WORKFLOW_NODE_PHASE1_DESIGN.md` 기준 2차 권장 항목은 아래 5개였다.

1. `Orchestrator`에 `workflow_id` 인자 수용
2. 노드 타입별 executor registry 도입
3. 노드 단위 상태 저장(`node_runs` 형태)
4. 기본 플로우는 `default_linear_v1`로 호환 실행
5. 대시보드 노드 편집 UI(ReactFlow 등) 추가

---

## 3. 현재 상태 기준 재판단
위 5개 중 현재 상태는 아래처럼 보는 것이 맞다.

### 3.1 이미 일부 진행된 항목
- 기본 워크플로우 로딩 및 실행
- 검증 실패 시 fallback
- 앱별 workflow 매핑 API의 일부 기반

### 3.2 아직 핵심이 비어 있는 항목
- `workflow_id`를 job/app/track 단위로 명시적으로 선택하여 실행하는 경로
- `if node_type == ...` 분기 대신 registry 기반 executor 구조
- 노드별 실행 상태 저장과 복구
- 시각적 노드 편집 UI

즉, 2차는 "워크플로우 실행 전환의 실질적 완료"가 범위다.

---

## 4. 2차 목표
2차의 핵심 목표는 4가지다.

1. 어떤 workflow를 실행할지 명확히 선택할 수 있어야 한다.
2. 노드 실행 로직이 registry 기반으로 분리되어야 한다.
3. 각 노드의 실행 결과를 저장하고 복구 가능해야 한다.
4. 대시보드에서 workflow를 보고 편집할 최소 UI가 있어야 한다.

---

## 5. 2차 구현 범위

### 5.1 Workflow 선택 경로 명시화
목표:
- 기본 workflow만 읽는 구조를 넘어서, app/job 단위로 선택된 workflow를 실제 실행에 반영한다.

필수 작업:
- job 생성 시 `workflow_id`를 선택적으로 저장
- app 설정의 `workflow_id`를 실제 orchestrator가 조회
- 우선순위 규칙 정의
  - `job.workflow_id`
  - `app.workflow_id`
  - `default_workflow_id`
  - 최종 fallback: 고정 파이프라인

산출물:
- `JOB_WORKFLOW_RESOLUTION.md`
- `job.workflow_id` 필드 또는 동등 상태 저장 구조

성공 조건:
- 같은 시스템 내에서도 앱별로 다른 workflow가 실제 실행된다.

비범위:
- workflow 조건식 기반 자동 선택

### 5.2 Executor Registry 도입
목표:
- `orchestrator.py` 내부의 큰 `if/elif` 노드 분기문을 단계적으로 registry 기반으로 치환한다.

필수 작업:
- `node_type -> executor callable` registry 추가
- 공통 실행 context 표준화
- 지원하지 않는 노드 타입 처리 정책 통일
- soft migration: 기존 분기문 fallback 유지

산출물:
- `app/workflow_registry.py`
- executor registration contract

성공 조건:
- 새 노드 타입 추가 시 orchestrator 핵심 분기문 수정 없이 registry에 등록하는 방식으로 확장 가능하다.

비범위:
- 모든 stage를 한 번에 외부 모듈로 완전 분리

### 5.3 Node Run 상태 저장
목표:
- workflow 전체 상태뿐 아니라 각 노드의 시작/성공/실패를 저장한다.

필수 작업:
- `node_runs` 또는 동등한 저장 구조 추가
- 저장 필드 정의
  - `job_id`
  - `workflow_id`
  - `node_id`
  - `node_type`
  - `status`
  - `started_at`
  - `finished_at`
  - `error_message`
  - `attempt`
- workflow 실행 로그와 node run의 연결
- 실패한 노드 확인 API 추가

산출물:
- `NODE_RUNS_SCHEMA.md`
- node run persistence code
- `/api/jobs/{job_id}/node-runs`

성공 조건:
- 운영자가 어떤 job이 어느 노드에서 실패했는지 바로 알 수 있다.
- 향후 stuck recovery와 resume의 기반이 생긴다.

비범위:
- 노드 중간 지점에서 완전한 resume 실행

### 5.4 Dashboard Workflow 편집 UI 최소 버전
목표:
- JSON만 직접 수정하는 단계를 넘어, 최소한의 노드 목록/엣지 편집 UI를 제공한다.

필수 작업:
- workflow 목록 조회 UI
- workflow 상세 조회 UI
- node 추가/수정/삭제 폼
- edge 추가/삭제 폼
- validate 후 save
- default workflow 지정 UI

산출물:
- workflow editor panel
- validation error viewer

성공 조건:
- 운영자가 JSON 파일을 직접 열지 않고도 workflow를 수정할 수 있다.

비범위:
- 1차 2차에서는 ReactFlow 수준의 완전한 drag-and-drop 에디터를 필수로 두지 않는다.
- 먼저 form-based editor를 목표로 한다.

### 5.5 실행 추적/운영 가시성
목표:
- workflow 전환 후 운영자가 상태를 추적할 수 있어야 한다.

필수 작업:
- job 상세 화면에 workflow id 표시
- 현재 실행 중 node 표시
- 실패 node 표시
- workflow validation failure / fallback reason 표시

산출물:
- job detail workflow panel
- fallback reason log section

성공 조건:
- workflow가 왜 fallback 되었는지 UI에서 확인 가능하다.

비범위:
- 고급 시계열 대시보드

---

## 6. 2차에서 하지 않을 것
이번 2차에서는 아래를 명시적으로 제외한다.

- 병렬 노드 실행
- 조건 표현식 엔진
- 변수 맵핑 DSL
- 노드별 독립 재시도 정책 엔진
- 완전한 resume from failed node
- ReactFlow 수준의 고급 시각 편집기
- 분산 worker 환경에서의 workflow 동시성 제어

이 항목들은 3차 이후 범위다.

---

## 7. 권장 구현 순서

### Phase 2-A
- workflow selection resolution
- job/app/default 우선순위 적용
- job에 workflow id 표시

### Phase 2-B
- executor registry 도입
- 기존 `if/elif` 실행기와 병행 운영

### Phase 2-C
- node run 상태 저장
- node run API
- 대시보드 job 상세 연결

### Phase 2-D
- workflow 편집 UI 최소 버전
- validate/save/default 변경

### Phase 2-E
- fallback reason / current node / failure node 표시

---

## 8. 완료 기준
2차가 끝났다고 보려면 최소 아래가 되어야 한다.

1. app 또는 job 기준으로 workflow를 선택해 실제 실행할 수 있다.
2. 노드 타입 실행이 registry를 통해 확장 가능하다.
3. 각 node 실행 결과가 저장된다.
4. 대시보드에서 workflow를 최소 편집할 수 있다.
5. fallback과 실패 node를 운영자가 UI에서 확인할 수 있다.

---

## 9. 3차로 넘길 항목
3차 이후에 다룰 항목은 아래다.

1. 조건 분기 엔진
2. 변수 전달/템플릿 맵핑
3. loop/parallel 실행기
4. failed node resume
5. drag-and-drop editor
6. workflow version compare / migration
7. node-level retry policy

---

## 10. 한 줄 정리
1차가 "정의하고 저장하는 단계"였다면, 2차는 "선택하고 실행하고 추적하는 단계"다.
