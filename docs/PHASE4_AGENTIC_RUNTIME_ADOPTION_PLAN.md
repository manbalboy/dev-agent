# Phase 4 Agentic Runtime Adoption Plan

## 1. Purpose
- 이 문서의 목적은 현재 시스템에 이미 있는 `비선형 workflow / memory runtime / resume-recovery` 기반 위에, 부족한 `도구 사용 / semantic retrieval / agent subgraph runtime`을 외부 프레임워크로 작은 단위부터 도입하는 계획을 정리하는 것이다.
- 핵심 원칙은 `전면 교체`가 아니라 `영향 범위를 제한한 점진 도입`이다.
- 현재 orchestrator, job store, workflow editor, dashboard, node_runs 계약은 유지한다.

## 2. Current State Snapshot

| Area | Status | Already Exists | Missing |
| --- | --- | --- | --- |
| Non-linear workflow runtime | `DONE` | edge-driven workflow, `if_label_match`, `loop_until_pass`, node_runs, resume/manual retry | external graph runtime, subgraph composition |
| Tool usage runtime | `PARTIAL` | planner `TOOL_REQUEST`, role `skills`, role `allowed_tools`, `research_search` only | shared tool registry, multi-tool execution, tool trace standardization |
| Memory runtime backend | `PARTIAL` | SQLite `memory_runtime.db`, DB-first retrieval, feedback/ranking, backlog candidates | semantic vector retrieval, richer similarity search |
| Agent framework adoption | `NOT STARTED` | internal planner graph MVP, internal workflow executor | LangGraph runtime, MCP tool layer, vector DB integration |

## 3. Decision
- `비선형 workflow`는 이미 있으므로 상위 job orchestrator를 지금 당장 외부 프레임워크로 갈아엎지 않는다.
- `도구 사용`은 `MCP`를 기준 인터페이스로 도입한다.
- `semantic retrieval`은 `Qdrant`를 우선 도입한다.
- `agent subgraph runtime`은 `LangGraph`를 부분 도입한다.
- 도입 순서는 항상 `shadow -> opt-in -> default`로 간다.

## 4. What Already Exists

### 4.1 Non-linear Workflow
- 현재 workflow executor는 success/failure/always edge를 따라 이동한다.
- control node:
  - `if_label_match`
  - `loop_until_pass`
- resume/recovery:
  - failed-safe resume
  - manual resume
  - node run persistence

### 4.2 Limited Tool Usage
- planner는 `TOOL_REQUEST`를 통해 제한적으로 도구를 요청할 수 있다.
- role에는 `skills`, `allowed_tools` 메타가 있다.
- 현재 실제 허용 도구는 사실상 `research_search` 중심이다.

### 4.3 Memory Runtime Without Vector Search
- current source of truth:
  - SQLite `memory_runtime.db`
- current retrieval:
  - metadata filter + score/confidence/freshness 기반
  - DB-first, file fallback
- 아직 임베딩/ANN/vector similarity는 없다.

## 5. Gaps To Close

### 5.1 Tool Layer Gap
- 현재 구조는 `planner -> research_search` 단일 경로에 가깝다.
- 부족한 것:
  - 공통 tool registry
  - standard tool request/result schema
  - per-role tool trace
  - internal/external tool adapter 계층

### 5.2 Vector Retrieval Gap
- 현재 retrieval은 keyword/metadata/ranking 중심이다.
- 부족한 것:
  - memory summary embedding
  - semantic nearest-neighbor retrieval
  - filter + vector hybrid query
  - shadow precision/recall evaluation

### 5.3 Agent Runtime Gap
- planner graph MVP는 있지만, external stateful graph runtime은 없다.
- 부족한 것:
  - subgraph abstraction
  - durable graph checkpoints
  - graph-level trace/view
  - reusable agent loop runtime

## 6. Framework Decision

### 6.1 MCP For Tool Runtime
- Why:
  - tool interface를 표준화하기 쉽다.
  - internal tool과 external tool을 같은 모델로 다룰 수 있다.
  - 현재 role `allowed_tools` 구조를 거의 그대로 살릴 수 있다.
- Scope:
  - first use as `tool transport and registry layer`
  - do not hand over top-level orchestration to MCP

### 6.2 Qdrant For Vector Retrieval
- Why:
  - 현재 SQLite canonical memory를 유지하면서 vector search만 별도 계층으로 붙이기 쉽다.
  - local/dev 모드와 Docker 운영 모드를 둘 다 가져가기 쉽다.
  - metadata filter + semantic search 혼합이 잘 맞는다.
- Scope:
  - first use only for `memory_search`
  - SQLite stays source of truth

### 6.3 LangGraph For Subgraph Runtime
- Why:
  - 현재 내부 workflow를 전부 교체하지 않고 planner/recovery/tool loop부터 옮기기 좋다.
  - long-running, stateful, checkpointed agent subgraph에 맞다.
- Scope:
  - first use only for `planner subgraph` and `recovery subgraph`
  - current job queue / store / dashboard / node_runs stay unchanged

## 7. Adoption Principles
- top-level `Orchestrator.process_job()`는 유지한다.
- `JobStore`, `node_runs`, `resume_state`, workflow JSON schema는 유지한다.
- 새 프레임워크는 항상 adapter 뒤에 붙인다.
- rollout은 feature flag와 shadow mode로 시작한다.
- 기존 파일 artifact 계약(`MEMORY_SELECTION.json`, `MEMORY_CONTEXT.json`, `SEARCH_CONTEXT.md`)은 초기 단계에서 유지한다.

## 8. Low-Impact Rollout Plan

### Phase 4-A. Tool Boundary Normalization
- Goal:
  - 현재 흩어진 tool 호출 경로를 한 군데로 모은다.
- Small Slice:
  - `ToolRequest` / `ToolResult` 공통 dataclass
  - orchestrator 내부 tool execution entrypoint 하나로 통합
  - `research_search`를 registry 등록형으로 전환
- No Big Change:
  - 실제 도구는 여전히 기존 스크립트를 호출한다.
- Success:
  - 새 tool 추가가 orchestrator 본문 수정 없이 가능하다.

### Phase 4-B. MCP Client Shadow Adapter
- Goal:
  - MCP를 실제 runtime에 붙이되, 기존 실행 결과는 바꾸지 않는다.
- Small Slice:
  - `MCPToolClient` adapter 추가
  - feature flag: `mcp_tools_shadow`
  - 같은 요청을 internal tool과 MCP shadow 둘 다 실행하고 결과 비교만 기록
- No Big Change:
  - planner는 여전히 기존 `research_search` 결과를 사용한다.
- Success:
  - shadow trace가 남고, main execution path는 동일하다.

### Phase 4-C. Internal Tools As MCP Servers
- Goal:
  - 내부 도구를 MCP 방식으로 감싼다.
- First Tools:
  - `repo_search`
  - `log_lookup`
  - `memory_search`
- No Big Change:
  - initially dashboard/admin only or planner opt-in only
- Success:
  - internal tools가 registry + allowlist 기준으로 호출된다.

### Phase 4-D. Qdrant Shadow Index
- Goal:
  - semantic retrieval 인덱스를 쓰기 시작하되 read path는 아직 유지한다.
- Small Slice:
  - `memory_entries` summary/title/payload 핵심 필드를 embedding
  - Qdrant collection sync job
  - shadow retrieval 결과만 trace에 기록
- No Big Change:
  - actual prompt injection은 기존 DB retrieval 유지
- Success:
  - candidate overlap / divergence를 job trace로 비교할 수 있다.

### Phase 4-E. Qdrant Read Path For `memory_search`
- Goal:
  - semantic retrieval을 가장 좁은 영역에만 켠다.
- Small Slice:
  - `memory_search` route에만 vector retrieval 적용
  - metadata filter + top-k + score threshold
  - feature flag: `vector_memory_retrieval`
- No Big Change:
  - planner/coder/reviewer 전체 경로로 바로 확장하지 않는다.
- Success:
  - retrieval precision 문제를 memory_search 한정으로 먼저 조정할 수 있다.

### Phase 4-F. LangGraph Planner Subgraph
- Goal:
  - planner graph MVP를 LangGraph subgraph로 교체한다.
- Small Slice:
  - nodes:
    - draft_plan
    - evaluate_plan
    - optional_tool_request
    - refine_plan
  - output contract:
    - existing `PLAN.md`
    - existing `PLAN_QUALITY.json`
- No Big Change:
  - stage 이름, 로그 산출물, dashboard 계약은 유지한다.
- Success:
  - planner graph 내부만 교체되고 상위 orchestration은 그대로 유지된다.

### Phase 4-G. LangGraph Recovery Subgraph
- Goal:
  - 현재 `hard gate / recovery / fix retry` 묶음을 LangGraph subgraph로 올린다.
- Small Slice:
  - nodes:
    - run_tests
    - analyze_failure
    - decide_recoverable
    - fix_once
    - retest
  - output contract:
    - same failure markdown
    - same stage transitions
- No Big Change:
  - current recovery policy와 error messages 유지
- Success:
  - recovery runtime만 graph로 옮겨도 job/store/UI 계약은 깨지지 않는다.

## 9. Recommended Execution Order
1. `4-A Tool Boundary Normalization`
2. `4-B MCP Client Shadow Adapter`
3. `4-C Internal Tools As MCP Servers`
4. `4-D Qdrant Shadow Index`
5. `4-E Qdrant Read Path For memory_search`
6. `4-F LangGraph Planner Subgraph`
7. `4-G LangGraph Recovery Subgraph`

## 10. First Implementation Target
- 첫 구현은 `4-A Tool Boundary Normalization`이다.
- 이유:
  - 현재 구조를 거의 안 건드리고 도입할 수 있다.
  - MCP/Qdrant/LangGraph 모두 tool/result contract가 먼저 있어야 붙이기 쉽다.
  - planner의 `research_search`를 registry 기반으로 바꾸는 것만으로도 다음 단계 연결점이 생긴다.

## 11. Non-Goals
- top-level job queue를 LangGraph로 즉시 교체하지 않는다.
- SQLite source-of-truth를 바로 없애지 않는다.
- vector retrieval을 모든 route에 한 번에 켜지 않는다.
- 자율 실행/automerge/self-issued PR까지 바로 가지 않는다.

## 12. Acceptance Criteria
- 운영자는 어떤 영역이 internal runtime이고 어떤 영역이 external framework인지 trace에서 구분할 수 있다.
- 새 framework 도입 후에도 existing workflow/job detail/node_runs UI가 깨지지 않는다.
- feature flag로 framework path를 즉시 off 할 수 있다.
- 첫 도입 단계는 테스트 회귀와 산출물 계약을 유지한다.

## 13. Official References
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
