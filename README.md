# AgentHub (FastAPI MVP)

AgentHub는 GitHub Issue에 `agent:run` 라벨이 붙으면 자동으로 작업을 수행해 PR을 만드는 도구입니다.

핵심 철학은 간단합니다.

- **순서를 정하는 주체는 AI가 아니라 워커 코드**입니다.
- AI(Gemini/Codex/Claude)는 필요할 때 CLI로 호출되는 작업자입니다.
- 따라서 `이슈 읽기 → 계획 → 구현 → 리뷰 → 수정 → 테스트 → PR` 순서를 코드가 강제합니다.

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

```bash
cd /home/docker/agentHub
bash scripts/setup_local_config.sh
```

원하면 값 덮어쓰기:
```bash
bash scripts/setup_local_config.sh --repo owner/repo --secret "내_시크릿"
```

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
4. `plan_with_gemini`
5. `implement_with_codex`
6. `test_after_implement`
7. `commit_implement`
8. `review_with_gemini`
9. `fix_with_codex`
10. `test_after_fix`
11. `commit_fix`
12. `push_branch`
13. `create_pr`
14. `finalize`

성공 시 `done`, 실패 시 최대 3회 재시도 후 `failed` 처리합니다.

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
- `escalation` (Claude, 기본 루프 밖 옵션)

템플릿에서 사용할 수 있는 주요 변수:

- `{prompt_file}`
- `{plan_path}` / `{review_path}` / `{spec_path}` / `{status_path}`
- `{repository}` / `{issue_number}` / `{branch_name}`
- `{work_dir}`

## 11) 초보자용 문제 해결

1. 웹훅이 401이면
- Secret 불일치 가능성이 큽니다.
- GitHub Webhook Secret과 `.env` 값을 같은지 확인하세요.

2. PR이 안 만들어지면
- `gh auth status`로 로그인 상태 확인
- Worker 로그(`/logs/<job_log_file>`)에서 실패 명령 확인

3. AI 단계가 실패하면
- 해당 CLI 로그인 상태 확인 (`gemini`, `codex`, 필요시 `claude`)
- `config/ai_commands.json` 템플릿이 실제 CLI 옵션과 맞는지 확인

## 12) 나중에 확장하기

- 저장소: 기본 `SQLiteJobStore`에서 Postgres 등으로 확장
- 워커: 멀티 워커/분산 큐 도입
- 대시보드: SSE/WebSocket 실시간 로그
