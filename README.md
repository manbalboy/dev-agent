# AgentHub (FastAPI MVP)

AgentHub는 GitHub Issue에 `agent:run` 라벨이 붙으면 자동으로 작업을 수행해 PR을 만드는 도구입니다.

핵심 철학은 간단합니다.

- **순서를 정하는 주체는 AI가 아니라 워커 코드**입니다.
- 현재 주 실행기는 **Gemini + Codex**입니다.
- 레거시 `Claude` / `Copilot` 이름은 일부 호환 경로에 남아 있어도 실제 기본 보조 실행은 Codex로 수렴합니다.
- 따라서 `이슈 읽기 → 계획 → 구현 → 리뷰 → 수정 → 테스트 → PR` 순서를 코드가 강제합니다.

## 문서 우선순위

문서가 많아졌기 때문에 아래 순서를 현재 기준 source-of-truth로 봅니다.

1. `README.md`
   - 설치, 운영, 기본 사용 흐름
2. `docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md`
   - 현재 상위 로드맵과 phase별 우선순위
3. `docs/PHASE*.md`, `docs/UI_QUALITY_AND_OPERATOR_INPUTS_PLAN.md`, `docs/FUTURE_DIRECTION_PRIORITY_ROADMAP.md`
   - 구현 상태와 다음 작은 슬라이스
   - 모바일 앱 분류 작업 기준은 `docs/MOBILE_APP_DEVELOPMENT_MODE_RULESET.md`
4. `PROJECT_FEATURES_SUMMARY.md`
   - 전체 구조를 훑는 snapshot 문서이며, 세부 최신 상태는 위 문서를 우선합니다.

문서 맵:
- [docs/DOCUMENT_MAP.md](./docs/DOCUMENT_MAP.md)
- 모바일 앱 개발 모드 기준:
  - [docs/MOBILE_APP_DEVELOPMENT_MODE_RULESET.md](./docs/MOBILE_APP_DEVELOPMENT_MODE_RULESET.md)

## 현재 상태 메모

- 핵심 구조 리팩터는 진행 중입니다.
- 현재 분리된 주요 런타임:
  - `app/assistant_runtime.py`
  - `app/agent_config_runtime.py`
  - `app/summary_runtime.py`
  - `app/content_stage_runtime.py`
  - `app/review_fix_runtime.py`
  - `app/planner_runtime.py`
  - `app/implement_runtime.py`
  - `app/workflow_node_runtime.py`
  - `app/workflow_pipeline_runtime.py`
  - `app/provider_runtime.py`
  - `app/preview_runtime.py`
  - `app/app_type_runtime.py`
  - `app/product_definition_runtime.py`
  - `app/improvement_runtime.py`
  - `app/ux_review_runtime.py`
  - `app/workspace_repository_runtime.py`
  - `app/job_failure_runtime.py`
  - `app/workflow_resolution_runtime.py`
  - `app/docs_snapshot_runtime.py`
  - `app/dashboard_job_runtime.py`
  - `app/dashboard_roles_runtime.py`
- `app/dashboard_runtime_input_runtime.py`
- `app/dashboard_admin_metrics_runtime.py`
- `app/runtime_recovery_trace.py`
- `app/failure_classification.py`
- `app/retry_policy.py`
- `app/provider_failure_counter_runtime.py`
- `app/requeue_reason_runtime.py`
- 현재 큰 파일 상태:
  - `app/dashboard.py`: `3637` lines
  - `app/orchestrator.py`: `6068` lines
- 따라서 현재 우선순위는 `새 기능 확대`보다 `구조 리스크 축소 + 운영 신뢰성 강화`입니다.
- 운영 신뢰성 쪽은 `failure classification`, `class-aware retry`, `needs_human`, `provider cooldown`, `dead-letter`, `dead-letter 재큐잉 액션`, `dead-letter operator note trail`까지 baseline이 올라왔습니다.
- 운영 신뢰성 쪽은 `provider_quarantined`, `provider_circuit_open`, `planner/reviewer alternate route fallback`, `worker startup sweep trace`, `restart-safe requeue reason`, `running node/job mismatch audit` baseline까지 올라왔고, 반복 `provider_timeout/tool_failure` burst는 이제 cooldown을 넘어 명시적 격리, circuit-breaker, alternate path로 전이될 수 있습니다.
- stale auto-recovery, dead-letter 재큐잉, 수동 workflow retry는 이제 같은 `requeue_reason_summary` shape로 trace/API/UI에 남고, worker 시작 시 mismatch audit 결과도 startup sweep trace와 admin metrics에 함께 남습니다.
- admin 운영 지표는 이제 `Dead Letter 작업`, `최근 복구 이력`, `공급자 장애 이력`, `재시작 감사 이력`, `복구 액션 그룹`, `운영자 조치 이력`을 같이 보여주고, `Dead Letter 작업`과 `최근 복구 이력`은 앱/실패 분류/공급자/결정/단계군 기준으로 바로 필터링할 수 있습니다.
- 다음 우선순위는 남은 runtime 분리, 특히 memory helper 쪽 축소와 self-growing bridge 효과성 검증입니다.

## 1) 무엇이 동작하나요?

1. GitHub Webhook(`issues`)를 받습니다.
2. `X-Hub-Signature-256` HMAC 서명을 검증합니다.
3. `agent:run` 라벨 이벤트면 Job을 생성해 큐에 넣습니다.
4. 별도 Worker 프로세스가 큐에서 Job을 꺼내 단계별로 실행합니다.
5. 상태/단계/로그/PR URL을 저장합니다.
6. 웹 대시보드에서 진행 상황을 봅니다.

## 2) 화면(대시보드)

- `/` : Job 목록 (status, stage, issue 링크, PR 링크)
- `/jobs/{job_id}` : Job 상세
- `/logs/{file_name}` : 로그 텍스트

## 3) 폴더 구조

```text
/home/docker/agentHub/
├─ app/
│  ├─ main.py
│  ├─ worker_main.py
│  ├─ config.py
│  ├─ models.py
│  ├─ store.py
│  ├─ github_webhook.py
│  ├─ orchestrator.py
│  ├─ command_runner.py
│  ├─ prompt_builder.py
│  ├─ dashboard.py
│  ├─ assistant_runtime.py
│  ├─ agent_config_runtime.py
│  ├─ summary_runtime.py
│  ├─ content_stage_runtime.py
│  ├─ review_fix_runtime.py
│  ├─ planner_runtime.py
│  ├─ implement_runtime.py
│  ├─ workflow_node_runtime.py
│  ├─ workflow_pipeline_runtime.py
│  ├─ provider_runtime.py
│  ├─ preview_runtime.py
│  ├─ app_type_runtime.py
│  ├─ product_definition_runtime.py
│  ├─ improvement_runtime.py
│  ├─ ux_review_runtime.py
│  ├─ workflow_resolution_runtime.py
│  ├─ docs_snapshot_runtime.py
│  ├─ dashboard_job_runtime.py
│  ├─ dashboard_runtime_input_runtime.py
│  ├─ dashboard_admin_metrics_runtime.py
│  ├─ runtime_recovery_trace.py
│  ├─ failure_classification.py
│  ├─ templates/
│  └─ static/
├─ config/
│  └─ ai_commands.example.json
├─ data/
│  ├─ jobs.json
│  ├─ queue.json
│  └─ logs/
├─ systemd/
│  ├─ agenthub-api.service
│  └─ agenthub-worker.service
├─ tests/
├─ .env.example
├─ requirements.txt
└─ README.md
```

## 4) 빠른 시작 (복붙용)

### 4-0. 제일 쉬운 자동 설정 (추천)

아래 2줄만 실행하면 `.env`와 `ai_commands.json`이 자동으로 생성됩니다.
기본값:
- repo: `manbalboy/agent-hub`
- branch: `main`
- test command: `echo skip tests`
- webhook secret: 자동 랜덤 생성

주의:
- `.env`, `.webhook_secret.txt` 같은 로컬 시크릿 파일은 절대 커밋하지 않습니다.
- 보안 이슈 제보 절차는 [SECURITY.md](./SECURITY.md)를 따릅니다.
- 실제 로테이션/히스토리 정리 절차는 [docs/SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md](./docs/SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md)를 따릅니다.

```bash
cd /home/docker/agentHub
bash scripts/setup_local_config.sh
```

원하면 값 덮어쓰기:
```bash
bash scripts/setup_local_config.sh --repo owner/repo --secret "내_시크릿"
```

위험 모드는 기본 비활성화입니다. 자동 우회 플래그가 꼭 필요한 trusted 개발 환경에서만 아래처럼 명시적으로 켭니다.

```bash
bash scripts/setup_local_config.sh --danger-mode
```

모바일 앱 실행 프리셋:

```bash
bash scripts/workspace_app.sh start --app myapp --repo owner/repo --mode expo-android
bash scripts/workspace_app.sh start --app myapp --repo owner/repo --mode expo-ios
bash scripts/workspace_app.sh start --app myapp --repo owner/repo --mode rn-android
bash scripts/workspace_app.sh start --app myapp --repo owner/repo --mode rn-ios
```

운영 지표의 `앱 실행 상태` 카드에서는 이 실행 메타를 읽어 최근 웹/모바일 앱 실행 모드와 마지막 명령을 바로 확인할 수 있습니다.
앱 분류 작업은 테스트 단계 이후 `_docs/MOBILE_APP_CHECKLIST.md`에 마지막 emulator/simulator 검증 요약을 자동으로 남깁니다.

그다음 systemd까지 자동 설치:

```bash
cd /home/docker/agentHub
sudo bash scripts/install_systemd.sh
```

### 4-1. 가상환경/패키지 설치

```bash
cd /home/docker/agentHub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4-2. 환경파일/AI 명령 템플릿 준비

```bash
cp .env.example .env
cp config/ai_commands.example.json config/ai_commands.json
```

`config/ai_commands.example.json`은 기본적으로 안전 모드 기준입니다. 위험한 CLI 우회 플래그는 필요할 때만 수동으로 opt-in 하세요.

`.env`에서 최소 아래 값은 반드시 맞춰주세요.

- `AGENTHUB_WEBHOOK_SECRET`
- `AGENTHUB_ALLOWED_REPOSITORY` (예: `owner/repo`)
- `AGENTHUB_DATA_DIR=/home/docker/agentHub/data`
- `AGENTHUB_WORKSPACE_DIR=/home/docker/agentHub/workspaces`
- `AGENTHUB_COMMAND_CONFIG=/home/docker/agentHub/config/ai_commands.json`
- `AGENTHUB_ENABLE_STAGE_MD_COMMITS=true` (단계별 `*.md` 변경 자동 docs 커밋)
- `AGENTHUB_API_PORT=8321`
- `AGENTHUB_STORE_BACKEND=sqlite` (또는 `json`)
- `AGENTHUB_SQLITE_FILE=/home/docker/agentHub/data/agenthub.db`

### 4-3. API 실행

```bash
cd /home/docker/agentHub
source .venv/bin/activate
set -a && source .env && set +a
uvicorn app.main:app --host 0.0.0.0 --port 8321
```

### 4-4. Worker 실행 (다른 터미널)

```bash
cd /home/docker/agentHub
source .venv/bin/activate
set -a && source .env && set +a
python -m app.worker_main
```

## 5) GitHub Webhook 설정

GitHub 저장소 → Settings → Webhooks → Add webhook

- Payload URL: `https://<your-host>/webhooks/github`
- Content type: `application/json`
- Secret: `.env`의 `AGENTHUB_WEBHOOK_SECRET`와 동일
- Which events: `Issues`

이제 Issue에 `agent:run` 라벨을 달면 Job이 생성됩니다.

## 6) 오케스트레이션 순서 (코드로 고정)

Worker는 아래 순서를 **항상 같은 방식**으로 실행합니다.

1. `prepare_repo`
2. `read_issue`
3. `write_spec`
4. `idea_to_product_brief`
5. `generate_user_flows`
6. `define_mvp_scope`
7. `architecture_planning`
8. `project_scaffolding`
9. `plan_with_gemini`
10. `design_with_codex`
11. `publish_with_codex`
12. `copywriter_with_codex`
13. `documentation_with_claude`
14. `implement_with_codex`
15. `code_change_summary`
16. `test_after_implement`
17. `commit_implement`
18. `review_with_gemini`
19. `product_review`
20. `improvement_stage`
21. `fix_with_codex`
22. `test_after_fix`
23. `commit_fix`
24. `push_branch`
25. `create_pr`
26. `finalize`

성공 시 `done`, 실패 시 최대 3회 재시도 후 `failed` 처리합니다.

## 6-1) Planner Graph MVP (확장형 플랜 루프)

`plan_with_gemini` 단계는 기본적으로 아래 루프로 동작합니다.

1. 초안 PLAN 작성
2. 품질 평가(`PLAN_QUALITY.json`)
3. 부족 섹션 보강 지시 후 재작성(최대 N회)
4. 통과 시 다음 단계 진행, 미통과여도 non-blocking으로 진행

플래너가 정보 부족을 감지하면 `TOOL_REQUEST`를 출력하고, 오케스트레이터가
`research_search`를 실행한 뒤 결과(`SEARCH_CONTEXT.md`)를 주입해 같은 라운드를 재실행합니다.
검색 API 실패 시에는 SPEC/README 기반 로컬 폴백 근거팩으로 계속 진행합니다.

환경 변수:

- `AGENTHUB_PLANNER_GRAPH_ENABLED=true|false`
- `AGENTHUB_PLANNER_GRAPH_MAX_ROUNDS=1..5` (기본 3)
- `AGENTHUB_HARD_GATE_MAX_ATTEMPTS=1..5` (기본 3)
- `AGENTHUB_HARD_GATE_TIMEBOX_SECONDS=120..7200` (기본 1200)
- `AGENTHUB_TEST_COMMAND_TIMEOUT_SECONDS=0..7200` (기본 900, 0이면 비활성화)

테스트 단계는 하드 게이트로 동작합니다.
- 실패 시 제한된 횟수 안에서만 수정/재테스트를 수행
- 같은 실패 시그니처 반복 시 즉시 중단
- 타임박스 초과 시 중단 (무한 루프 방지)

## 7) 실패 처리 정책

- 실패 원인은 로그 파일에 단계별로 남깁니다.
- 최종 실패 시 `STATUS.md`를 저장소에 남기고,
- 가능하면 WIP Draft PR 생성도 시도합니다.
- 자동 머지는 절대 하지 않습니다.

## 8) systemd 예시 (24/7 실행)

```bash
sudo cp systemd/agenthub-api.service /etc/systemd/system/
sudo cp systemd/agenthub-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agenthub-api
sudo systemctl enable --now agenthub-worker
```

로그 확인:

```bash
sudo journalctl -u agenthub-api -f
sudo journalctl -u agenthub-worker -f
```

## 9) 테스트 실행

```bash
cd /home/docker/agentHub
source .venv/bin/activate
pytest -q
```

## 9-1) 실환경 E2E 웹훅 테스트 (done/failed 확인)

API/Worker가 실행 중인 상태에서 아래 스크립트로 실제 웹훅 흐름을 검증할 수 있습니다.

```bash
cd /home/docker/agentHub
bash scripts/test_live_webhook.sh --issue <실제_이슈번호>
```

이 스크립트는 다음을 자동으로 확인합니다.

1. `/healthz` 응답 확인
2. `.env`의 `AGENTHUB_WEBHOOK_SECRET`으로 서명한 웹훅 전송
3. Job 생성(`accepted: true`) 확인
4. 저장소(JSON/SQLite)를 폴링해 최종 상태가 `done` 또는 `failed`가 될 때까지 대기

옵션 예시:

```bash
bash scripts/test_live_webhook.sh --issue 123 --timeout 600 --poll 2
```

실패 시 해당 Job 로그(`data/logs/<job_id>.log`) 마지막 40줄을 자동 출력합니다.

## 10) AI 명령 템플릿 설명

`config/ai_commands.json`은 운영 환경 CLI에 맞게 수정해야 합니다.

예시 키:

- `planner` (Gemini)
- `coder` (Codex)
- `reviewer` (Gemini)
- `escalation` (Codex helper, 기본 루프 밖 보조 분석)

템플릿에서 사용할 수 있는 주요 변수:

- `{prompt_file}`
- `{plan_path}` / `{review_path}` / `{spec_path}` / `{status_path}`
- `{repository}` / `{issue_number}` / `{branch_name}`
- `{work_dir}`

## 11) AI 도우미/역할관리 사용법 (실무 순서)

대시보드 기준으로 아래 순서대로 쓰면 됩니다.

1. 설정 → `AI 템플릿`
- `planner`, `coder`, `reviewer`, `copilot` 템플릿 저장

2. 설정 → `역할 관리`
- 역할별 `CLI`, `template_key`, 입력/출력 정의
- 프리셋(역할 묶음) 생성 후 이슈 등록 시 선택

3. 메인 → `AI 도우미`
- 런 ID/에러 상황을 넣고 진단 요청
- 권장 흐름: `분석 → 제안 명령 확인 → 승인 후 실행`

4. Job 상세
- `기존 로그` 탭: 원본 실행 로그
- `작업별 로그 + MD` 탭: stage별 로그와 당시 md 스냅샷

## 12) Codex/Gemini 사전 점검

아래가 준비되지 않으면 AI 단계가 쉽게 실패합니다.

```bash
gh auth status
gemini --version
codex --version
```

`Codex 실행 실패: No such file or directory: 'codex'`가 뜨면:

1. `codex` 설치 또는 PATH 등록
2. 필요하면 `.env`에 절대경로 지정

```bash
AGENTHUB_CODEX_BIN=/usr/local/bin/codex
```

3. 서비스 재시작 후 재시도

## 13) 서비스 모드 운영(systemd)

환경마다 유닛명이 다를 수 있으므로 먼저 확인:

```bash
systemctl list-units --type=service --all | rg -i "agenthub|devflow"
```

예시 유닛:
- `agenthub-api.service`
- `agenthub-worker.service`
- `agenthub-dashboard-next.service`
- `devflow-agenthub-api.service`
- `devflow-agenthub-web.service`

재시작 예시:

```bash
sudo systemctl restart agenthub-api.service
sudo systemctl restart agenthub-worker.service
```

## 14) 초보자용 문제 해결

1. 웹훅이 401이면
- Secret 불일치 가능성이 큽니다.
- GitHub Webhook Secret과 `.env` 값을 같은지 확인하세요.

2. PR이 안 만들어지면
- `gh auth status`로 로그인 상태 확인
- Worker 로그(`/logs/<job_log_file>`)에서 실패 명령 확인

3. AI 단계가 실패하면
- 해당 CLI 로그인 상태 확인 (`gemini`, `codex`)
- `config/ai_commands.json` 템플릿이 실제 CLI 옵션과 맞는지 확인

4. 보조 분석 단계가 실패하면
- `config/ai_commands.json`에서 `codex_helper`, `escalation` 템플릿 확인
- 대시보드 `현재 모델 확인`에서 `DANGER/SAFE` 상태와 위험 템플릿 키 확인

## 15) 나중에 확장하기

- 저장소: 기본 `SQLiteJobStore`에서 Postgres 등으로 확장
- 워커: 멀티 워커/분산 큐 도입
- 대시보드: SSE/WebSocket 실시간 로그
