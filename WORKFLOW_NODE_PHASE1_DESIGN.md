# Workflow Node 전환 1차 설계 문서

## 1. 배경
현재 AgentHub는 `app/orchestrator.py`에 고정된 선형 파이프라인으로 동작한다.

목표는 n8n 스타일처럼:
- 노드/엣지로 워크플로우를 시각적으로 구성
- 저장된 워크플로우 정의를 기반으로 실행
- 앱/트랙별로 다른 플로우 적용 가능

단, 이번 1차는 **실행 엔진 전면 전환 전 단계**로 설계/저장/검증을 먼저 도입한다.

---

## 2. 이번 턴에서 확정한 방향
- 고정 워크플로우를 즉시 제거하지 않는다.
- 1차는 아래 3가지를 먼저 구현한다.
  1. 워크플로우 스키마 정의
  2. 워크플로우 저장소(JSON) 도입
  3. 워크플로우 검증/조회 API 도입

---

## 3. 1차 구현 범위(완료)

### 3.1 스키마/검증 모듈
- 파일: `app/workflow_design.py`
- 제공 기능:
  - 지원 노드 타입 정의(`SUPPORTED_NODE_TYPES`)
  - 기본 템플릿(`default_workflow_template`)
  - 스키마 정보 응답(`schema_payload`)
  - 저장/로딩(`save_workflows`, `load_workflows`)
  - 검증(`validate_workflow`)
    - 노드 중복 ID 검사
    - 엣지 from/to 유효성 검사
    - edge 이벤트 타입 검사(success/failure/always)
    - 진입 노드(entry_node_id) 유효성 검사
    - 사이클 검사(DAG 여부)

### 3.2 워크플로우 설정 파일
- 파일: `config/workflows.json`
- 기본 워크플로우 `default_linear_v1` 포함
- 현재 고정 오케스트레이션을 노드/엣지로 표현한 템플릿 탑재

### 3.3 API 추가 (`app/dashboard.py`)
- `GET /api/workflows/schema`
- `GET /api/workflows`
- `POST /api/workflows/validate`
- `POST /api/workflows` (저장/업데이트)

---

## 4. 워크플로우 데이터 형식(v1)
```json
{
  "workflow_id": "default_linear_v1",
  "name": "Default Linear V1",
  "description": "현재 고정 오케스트레이션과 동등한 1차 노드 템플릿",
  "version": 1,
  "entry_node_id": "n1",
  "nodes": [
    { "id": "n1", "type": "gh_read_issue", "title": "이슈 읽기" }
  ],
  "edges": [
    { "from": "n1", "to": "n2", "on": "success" }
  ]
}
```

---

## 5. 현재 한계(의도된 범위 제한)
- 아직 실행 엔진은 기존 `Orchestrator` 고정 플로우를 사용
- 노드 에디터 UI(드래그/연결)는 미구현
- 조건 분기 표현식, 변수 맵핑, 병렬 노드 실행은 미구현

---

## 6. 다음 단계(2차 권장)
1. Orchestrator에 `workflow_id` 인자 수용
2. 노드 타입별 executor registry 도입
3. 노드 단위 상태 저장(`node_runs` 형태)
4. 기본 플로우는 `default_linear_v1`로 호환 실행
5. 대시보드 노드 편집 UI(ReactFlow 등) 추가

---

## 7. 운영 규칙 제안
- 변경 중에도 항상 fallback:
  - 워크플로우 로딩 실패 시 `default_linear_v1` 또는 기존 고정 플로우 사용
- 워크플로우 저장 시 반드시 validate 성공 후 반영
- 앱별 실험 플로우는 `workflow_id` 버전 태깅(`mvp-1_v1`, `mvp-1_v2`)

