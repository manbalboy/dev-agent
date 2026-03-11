# Phase 4 Self-Growing Agentic Runtime Plan

## 1. North Star
- Phase 4의 목표는 이 시스템을 `주어진 issue를 처리하는 자동 실행기`에서 `알아서 성장하는 프로그램 개발자 동료`로 한 단계 더 끌어올리는 것이다.
- 그 상태의 시스템은 아래를 동시에 수행해야 한다.
  - 현재 repo와 workflow 상태를 이해한다.
  - 필요한 도구를 안전하게 사용한다.
  - 과거 memory를 semantic retrieval로 참고한다.
  - 선형 파이프라인이 아니라 비선형 graph/subgraph로 적절한 경로를 선택한다.
  - 다음 개선 과제를 스스로 제안하고, 운영자 승인 아래 다음 작업으로 연결한다.

## 2. Scope Of Phase 4
- Phase 4는 아래 3개 축을 한 번에 묶는 phase다.
  - `도구 사용 런타임`
  - `벡터 DB 기반 semantic retrieval`
  - `비선형/상태형 agent runtime`
- 이 phase는 top-level orchestrator를 외부 프레임워크로 갈아엎는 phase가 아니다.
- 현재 job queue, store, dashboard, workflow editor, node_runs 계약은 유지한다.

## 3. Non-Negotiable Principles

### 3.1 점진적 개발
- 한 번에 큰 구조를 바꾸지 않는다.
- 항상 `shadow -> opt-in -> default` 순서로 간다.
- 새 framework path는 feature flag로 즉시 끌 수 있어야 한다.

### 3.2 소규모 단위 작업
- 한 슬라이스는 한 가지 책임만 도입한다.
- 한 슬라이스는 되도록 한 화면, 한 API, 한 runtime adapter, 한 trace 파일 수준으로 자른다.
- 새 framework 도입은 항상 기존 계약을 감싸는 adapter 형태로만 시작한다.

### 3.3 테스트 우선
- 모든 슬라이스는 아래 3종 테스트를 기본으로 가진다.
  - unit test
  - contract/regression test
  - feature-flag off-path test
- 전체 회귀(`PYTHONPATH=. .venv/bin/pytest -q`)를 항상 마지막에 돈다.
- shadow path는 `기존 결과 불변`을 테스트로 보장해야 한다.

### 3.4 운영 가시성
- 새 기능은 trace가 없으면 넣지 않는다.
- operator는 최소한 아래를 봐야 한다.
  - 어떤 tool/runtime이 primary 인지
  - 어떤 tool/runtime이 shadow 인지
  - primary와 shadow가 어떻게 달랐는지

## 4. Current State Snapshot

| Area | Status | Already Exists | Missing |
| --- | --- | --- | --- |
| Non-linear workflow core | `DONE` | edge-driven workflow, `if_label_match`, `loop_until_pass`, node_runs, failed-safe resume, manual resume | external graph runtime, reusable subgraph abstraction |
| Tool boundary normalization | `PARTIAL` | `ToolRequest`, `ToolResult`, `ToolRuntime`, registry-style `research_search` path | more internal tools, typed tool arguments, per-tool policy surface |
| MCP shadow integration | `PARTIAL` | `MCPToolClient`, `mcp_tools_shadow` feature flag, `_docs/MCP_TOOL_SHADOW.jsonl` trace | real server wiring, listed tool validation in live env, operator view |
| Internal tool catalog | `PARTIAL` | `research_search` primary path, internal `log_lookup`, `repo_search`, `memory_search` primary handlers, role `skills`, role `allowed_tools` | live operator action wiring, MCP server wrapper promotion |
| Memory DB runtime | `PARTIAL` | SQLite `memory_runtime.db`, DB-first retrieval, feedback/ranking, backlog candidates, `memory_search` opt-in vector fallback path | wider semantic retrieval rollout, operator comparison UX |
| Vector DB | `PARTIAL` | SQLite source of truth, `VECTOR_SHADOW_INDEX.json` shadow manifest, vector candidate payload selection, optional Qdrant shadow upsert transport, optional semantic embedding provider adapter, `memory_search` vector query path with metadata filter + threshold | shadow retrieval comparison, broader vector read-path rollout |
| LangGraph/subgraph runtime | `PARTIAL` | internal planner graph MVP, internal workflow executor, optional `LANGGRAPH_PLANNER_SHADOW.json`, optional `LANGGRAPH_RECOVERY_SHADOW.json` traces behind feature flag | durable subgraph runtime, graph checkpoints, operator comparison view, broader rollout |
| UI quality / operator inputs | `PARTIAL` | planner/coder prompt guardrails, admin runtime input registry, prompt-safe `OPERATOR_INPUTS.json`, shell/template env bridge, job detail visibility, AI/템플릿 기반 request draft recommendation + operator approval path | benchmark artifact, richer template library, assistant/job-context 연결 강화 |

## 5. What Already Exists

### 5.1 비선형 구조는 이미 있다
- success/failure/always edge 기반 workflow executor가 있다.
- control-flow node:
  - `if_label_match`
  - `loop_until_pass`
- resume/recovery:
  - failed-safe resume
  - manual resume
  - node run persistence

### 5.2 도구 사용은 아직 제한적이지만 첫 internal tool이 붙었다
- planner는 `TOOL_REQUEST`를 통해 도구를 요청할 수 있다.
- role에는 `skills`, `allowed_tools` 메타가 있다.
- 현재 primary tool path는 `research_search`, `log_lookup`, `repo_search`, `memory_search`까지 늘어났지만, planner prompt는 아직 `research_search`만 직접 요청한다.
- `log_lookup`, `repo_search`, `memory_search`는 helper/admin opt-in 성격의 internal handler로 먼저 붙였다.

### 5.3 memory runtime은 있지만 semantic retrieval은 아직 좁은 surface에만 있다
- canonical source of truth:
  - SQLite `memory_runtime.db`
- retrieval:
  - metadata filter
  - score/confidence/freshness
  - DB-first, file fallback
- `memory_search` route 한정으로만 vector retrieval opt-in 실험이 있다.
- planner/reviewer/coder primary retrieval은 아직 embedding/vector similarity를 쓰지 않는다.

## 6. Why Phase 4 Exists
- Phase 3이 `memory를 durable runtime으로 올리는 단계`였다면,
- Phase 4는 `memory + tool + graph runtime`이 실제로 다음 행동을 더 잘 만들게 하는 단계다.
- 즉, Phase 4부터 비로소 아래 느낌이 나온다.
  - 로그를 읽고
  - repo를 찾고
  - memory를 semantic하게 참고하고
  - 선형이 아닌 경로로 조사/수정/검증을 반복하는
  - `개발자 동료다운` 실행

## 7. Framework Decision

### 7.1 MCP For Tool Runtime
- 역할:
  - tool transport
  - tool registry boundary
  - internal/external tool 공통 인터페이스
- 도입 이유:
  - 현재 role `allowed_tools` 구조를 살릴 수 있다.
  - shadow mode로 붙이기 쉽다.

### 7.2 Qdrant For Vector Retrieval
- 역할:
  - semantic memory retrieval
  - metadata + vector hybrid query
- 도입 이유:
  - SQLite source-of-truth를 유지하면서 vector search만 분리할 수 있다.
  - dev/local과 운영 전환이 비교적 단순하다.

### 7.3 LangGraph For Stateful Subgraphs
- 역할:
  - planner subgraph
  - recovery subgraph
  - 이후 tool-using diagnosis loops
- 도입 이유:
  - top-level orchestrator를 유지하면서 내부 loop만 교체하기 쉽다.
  - long-running, stateful, checkpointed subgraph에 맞다.

## 8. Phase 4 Architecture Direction

### 8.1 Keep
- `Orchestrator.process_job()`
- `JobStore`
- `workflow JSON schema`
- `node_runs`
- dashboard/job detail contracts

### 8.2 Add
- tool adapter layer
- MCP shadow/primary clients
- vector index sync layer
- LangGraph subgraph adapters

### 8.3 Do Not Do Yet
- top-level queue를 LangGraph로 교체
- SQLite source-of-truth 제거
- vector retrieval을 모든 route에 즉시 확대
- self-issued autonomous PR / automerge

## 9. Work Packages

### Phase 4-A. Tool Boundary Normalization
- Goal:
  - 흩어진 tool 경로를 공통 runtime으로 모은다.
- Small Slices:
  - `ToolRequest` / `ToolResult`
  - `ToolRuntime`
  - `research_search` registry 전환
- Current Status:
  - `PARTIAL`
- Done:
  - primary `research_search` path가 registry 기반으로 정리되었다.
- Remaining:
  - internal tool 2~3개 추가
  - typed payload/argument contract

### Phase 4-B. MCP Shadow Adapter
- Goal:
  - primary 결과는 유지한 채 MCP shadow trace만 붙인다.
- Small Slices:
  - `MCPToolClient`
  - feature flag `mcp_tools_shadow`
  - `_docs/MCP_TOOL_SHADOW.jsonl`
- Current Status:
  - `PARTIAL`
- Done:
  - shadow adapter와 trace 파일이 추가되었다.
- Remaining:
  - 실제 MCP server와 연결된 live validation
  - dashboard trace visibility

### Phase 4-C. Internal Tool Catalog
- Goal:
  - internal tools를 작은 단위부터 primary registry에 추가한다.
- First Tools:
  - `log_lookup`
  - `repo_search`
  - `memory_search`
- Principle:
  - 처음부터 planner 전체에 개방하지 않는다.
  - dashboard/admin or helper role opt-in부터 시작한다.
- Current Status:
  - `PARTIAL`
- Done:
  - `log_lookup` internal primary handler 추가
  - helper role 기본 allowlist에 `log_lookup` 연결
  - `repo_search` internal primary handler 추가
  - helper role 기본 allowlist에 `repo_search` 연결
  - `memory_search` internal primary handler 추가
  - helper role 기본 allowlist에 `memory_search` 연결
- Remaining:
  - dashboard/operator action wiring
  - 이후 MCP server wrapper는 별도 슬라이스로 분리

### Phase 4-C2. Operator Runtime Inputs
- Goal:
  - tool/vector/graph runtime이 실제 외부 서비스 의존 기능을 안전하게 수행할 수 있게 operator input을 연결한다.
- Small Slices:
  - runtime input request/value store
  - admin request/provide UI
  - prompt-safe artifact
  - shell/template env bridge
- Constraint:
  - secret 값은 prompt/log/UI에 평문 노출 금지
  - 자동 request generation은 operator approval 전까지 금지
- Status:
  - `PARTIAL`

### Phase 4-D. Qdrant Shadow Index
- Goal:
  - semantic retrieval 인덱스를 쓰기 시작하되 primary read path는 아직 유지한다.
- Small Slices:
  - embedding payload selection
  - Qdrant collection sync
  - shadow retrieval trace only
- Current Status:
  - `PARTIAL`
- Done:
  - feature flag `vector_memory_shadow`
  - `_docs/VECTOR_SHADOW_INDEX.json`
  - SQLite memory DB -> vector candidate payload selection
  - optional Qdrant shadow collection upsert trace
  - optional semantic embedding provider adapter (`hash` default, `openai` opt-in)
- Remaining:
  - shadow retrieval comparison trace
  - vector read-path rollout

### Phase 4-E. Qdrant Read Path For `memory_search`
- Goal:
  - 가장 좁은 영역에서만 vector retrieval을 primary로 켠다.
- Small Slices:
  - `memory_search` route only
  - metadata filter + top-k + threshold
  - feature flag `vector_memory_retrieval`
- Current Status:
  - `PARTIAL`
- Done:
  - feature flag `vector_memory_retrieval`
  - `memory_search` route only vector query path
  - metadata filter + top-k + threshold
  - `MEMORY_SEARCH_RESULT.json` source/fallback 표시
- Remaining:
  - operator comparison view
  - vector result quality shadow comparison
  - broader rollout decision

### Phase 4-F. LangGraph Planner Subgraph
- Goal:
  - planner graph MVP를 LangGraph subgraph로 교체한다.
- Nodes:
  - draft_plan
  - evaluate_plan
  - optional_tool_request
  - refine_plan
- Contract:
  - existing `PLAN.md`
  - existing `PLAN_QUALITY.json`
- Current Status:
  - `PARTIAL`
- Done:
  - feature flag `langgraph_planner_shadow`
  - `_docs/LANGGRAPH_PLANNER_SHADOW.json`
  - draft/evaluate/optional_tool_request/refine loop shadow trace
  - existing `PLAN.md`, `PLAN_QUALITY.json` contract 유지
- Remaining:
  - operator comparison view
  - primary planner graph와 LangGraph shadow divergence analysis
  - broader rollout decision

### Phase 4-G. LangGraph Recovery Subgraph
- Goal:
  - `run_tests -> analyze_failure -> decide_recoverable -> fix_once -> retest` 묶음을 LangGraph로 올린다.
- Contract:
  - existing failure markdown
  - existing stage transitions
  - existing recovery policies
- Current Status:
  - `PARTIAL`
- Done:
  - feature flag `langgraph_recovery_shadow`
  - `_docs/LANGGRAPH_RECOVERY_SHADOW.json`
  - analyze_failure/decide_recoverable/fix_once/retest shadow trace
  - existing retry policy 유지
- Remaining:
  - operator comparison view
  - primary recovery policy와 LangGraph shadow divergence analysis
  - broader rollout decision

### Phase 4-H. Self-Growing Companion Bridge
- Goal:
  - tool + vector retrieval + graph runtime이 실제 다음 행동을 더 잘 만들게 한다.
- Scope:
  - approved backlog -> next job enqueue bridge
  - recurring problem clustering input
  - tool-using diagnosis loops
- Current Status:
  - `PARTIAL`
- Done:
  - candidate `approve / queue / dismiss` operator action API
  - queued follow-up job 생성
  - `_docs/FOLLOWUP_BACKLOG_TASK.json` artifact
  - planner prompt가 follow-up backlog artifact를 최우선 next action으로 읽도록 연결
  - follow-up job contract 메타(`job_kind`, `parent_job_id`, `backlog_candidate_id`) 분리
  - `FAILURE_PATTERNS.json`의 반복 count 기반 backlog candidate ingest
- Remaining:
  - operator comparison/approval UX 고도화
  - diagnosis trace 장기 효과성/빈도 상관관계 고도화
- Done (latest slice):
  - feature flag `assistant_diagnosis_loop`
  - `_docs/ASSISTANT_DIAGNOSIS_TRACE.json`
  - assistant log-analysis 전에 `log_lookup -> repo_search -> memory_search` 순차 evidence pack 생성
  - assistant chat에도 동일한 diagnosis loop와 trace 응답 연결
  - job detail debug tab과 assistant panel에서 diagnosis trace 노출
  - admin panel에서 diagnosis trace scope/tool/recent trace 비교 뷰 제공
  - admin panel에서 selected/compare trace diff와 tool query/status 차이 노출
- Note:
  - 이는 Phase 3 backlog/runtime 위에 올라간다.
  - Phase 5 전에 해결하면 좋은 마지막 연결고리다. 운영 안정화 전에 `다음 행동 연결`을 먼저 닫아야 self-growing companion 목적이 선명해진다.

## 10. Recommended Execution Order
1. `4-A` 마감
2. `4-B` 가시화
3. `4-D` Qdrant shadow index
4. `4-E` vector read path for `memory_search`
5. `4-F` LangGraph planner subgraph
6. `4-G` LangGraph recovery subgraph
7. `4-H` self-growing companion bridge

## 11. Small-Slice Rule
- 각 슬라이스는 아래를 넘지 않는다.
  - runtime adapter 1개
  - feature flag 1개
  - trace artifact 1개
  - UI 변경 1개 이하
- 한 슬라이스에서 framework 2개를 동시에 primary path에 올리지 않는다.
- shadow mode로 먼저 들어간 path는 다음 슬라이스에서만 opt-in으로 승격할 수 있다.

## 12. Test Discipline

### 12.1 Every Slice Must Add Tests
- unit test 추가 필수
- 기존 contract regression test 유지 필수
- feature-flag off-path test 필수

### 12.2 Every Slice Must End With Full Regression
- minimum:
  - focused pytest
  - `python3 -m py_compile` for changed python modules
  - template/script syntax check when UI/js changed
  - full `PYTHONPATH=. .venv/bin/pytest -q`

### 12.3 What Must Be Proven
- primary path 결과가 안 바뀌었는지
- shadow path가 trace만 남기고 있는지
- feature flag off일 때 완전히 inert한지
- dashboard/job detail contracts가 깨지지 않는지

## 13. Acceptance Criteria
- operator는 어떤 path가 `primary`이고 어떤 path가 `shadow`인지 trace에서 구분할 수 있다.
- vector retrieval 도입 후에도 SQLite source-of-truth는 유지된다.
- LangGraph 도입 후에도 기존 job/store/node_runs UI 계약은 유지된다.
- Phase 4는 항상 작은 단위와 테스트 우선 원칙을 지킨다.

## 14. Official References
- LangGraph:
  - https://docs.langchain.com/oss/python/langgraph
  - https://docs.langchain.com/oss/python/langgraph/use-graph-api
- Qdrant:
  - https://qdrant.tech/documentation/quick-start/
  - https://qdrant.tech/documentation/search/
  - https://qdrant.tech/documentation/interfaces/
- MCP:
  - https://modelcontextprotocol.io/docs/concepts/tools
  - https://modelcontextprotocol.io/docs/sdk
  - https://modelcontextprotocol.io/examples
  - https://py.sdk.modelcontextprotocol.io/
