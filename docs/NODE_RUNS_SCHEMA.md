# Node Run Schema

## 목적
워크플로우 내부의 개별 노드(Node) 단위 실행 이력을 추적하고 저장한다. 이는 향후 특정 실패 노드에서의 재개(Resume) 및 운영 가시성 확보를 위한 기초 데이터가 된다.

## 데이터 구조 (NodeRunRecord)

| 필드명 | 타입 | 설명 |
| :--- | :--- | :--- |
| `node_run_id` | `str` (UUID) | 실행 이력의 고유 식별자 |
| `job_id` | `str` | 연결된 Job의 ID |
| `workflow_id` | `str` | 실행 당시 사용된 워크플로우 ID |
| `node_id` | `str` | 워크플로우 내 노드의 고유 ID |
| `node_type` | `str` | 노드의 타입 (예: `gemini_plan`, `codex_implement`) |
| `node_title` | `str` | 노드의 표시명 |
| `status` | `str` | 실행 상태 (`running`, `success`, `failed`) |
| `attempt` | `int` | Job의 실행 시도 횟수 (Attempt) |
| `started_at` | `str` (ISO8601) | 실행 시작 시각 (UTC) |
| `finished_at` | `str` (ISO8601) | 실행 종료 시각 (UTC, 선택 사항) |
| `error_message` | `str` | 실패 시 에러 메시지 (선택 사항) |
| `agent_profile` | `str` | 사용된 에이전트 프로필 (`primary`, `fallback`) |

## 저장소 방식별 구현

### 1. JSON (data/node_runs.json)
- `node_run_id`를 키로 하는 Map 구조로 저장된다.
- 파일 락킹(`fcntl`)을 통해 API와 워커 간의 동시 수정을 방지한다.

### 2. SQLite (node_runs 테이블)
- `node_run_id`가 Primary Key로 지정된다.
- `job_id`와 `started_at`에 대한 인덱스가 생성되어 빠른 조회를 지원한다.
- `ON CONFLICT(node_run_id) DO UPDATE`를 통해 Upsert 로직을 수행한다.

## 관련 API
- `GET /api/jobs/{job_id}/node-runs`: 특정 Job의 모든 노드 실행 이력을 조회한다.
- `GET /api/jobs/{job_id}`: Job 상세 정보와 함께 노드 실행 이력을 포함하여 반환한다.

## 활용 시나리오
1. **대시보드 가시성**: 사용자가 어떤 단계에서 얼마나 시간이 소요되었는지, 어떤 에러로 실패했는지 노드 단위로 확인 가능하다.
2. **Resume (재개)**: Job 재시도 시, 이미 성공한 노드는 건너뛰고 마지막 실패 지점부터 실행을 이어갈 수 있는 근거 자료로 활용된다.
3. **에이전트 성능 분석**: 특정 노드 타입이나 에이전트 프로필별 성공률/소요 시간 통계 산출이 가능하다.
