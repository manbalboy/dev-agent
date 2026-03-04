# AgentHub 기능 상세 요약 (현재 구현 기준)

## 1. 프로젝트 개요
AgentHub는 GitHub 이슈 라벨 이벤트(`agent:run`)를 받아, 고정된 오케스트레이션 파이프라인으로 작업을 수행하고 PR 생성까지 자동화하는 FastAPI 기반 시스템입니다.

핵심 원칙:
- 순서와 재시도 정책은 워커 코드가 결정
- AI CLI(Gemini/Codex/Claude)는 단계별 실행 도구
- 작업 상태/로그/결과를 JSON 스토어와 대시보드로 가시화

---

## 2. 아키텍처 구성
- API 서버: `app/main.py` (FastAPI)
- 워커: `app/worker_main.py`
- 오케스트레이터: `app/orchestrator.py`
- 저장소: `app/store.py` (`jobs.json`, `queue.json`)
- 웹훅 처리: `app/github_webhook.py`
- 대시보드/API: `app/dashboard.py`
- 명령 템플릿 실행기: `app/command_runner.py`

데이터 저장 위치:
- 작업 목록: `data/jobs.json`
- 큐: `data/queue.json`
- 로그: `data/logs/*.log`

---

## 3. 워크플로우(실행 파이프라인)
한 Job은 아래 순서로 실행됩니다.
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

재시도 정책:
- 실패 시 최대 `AGENTHUB_MAX_RETRIES`(기본 3) 재시도
- 최종 실패 시 `failed` 처리 + 상태/로그 기록
- 필요 시 WIP PR 생성 시도

---

## 4. 웹훅/이슈 트리거 기능
### 4.1 GitHub 웹훅 수신
- 엔드포인트: `POST /webhooks/github`
- 이벤트: `issues` + `labeled`
- HMAC 서명(`X-Hub-Signature-256`) 검증
- `agent:run` 라벨 조건 충족 시 Job 생성

### 4.2 대시보드 직접 이슈 등록
- 엔드포인트: `POST /api/issues/register`
- 동작:
  - GitHub 이슈 생성
  - 라벨 자동 부착: `agent:run`, `app:{app_code}`, `track:{track}`
  - Job 생성 + 큐 적재

중복 방지:
- 같은 이슈에 `queued/running` Job이 이미 있으면 신규 생성 대신 기존 Job 연결

---

## 5. 앱 분리(App Namespace) 기능
### 5.1 앱 등록 관리
- `GET /api/apps`: 앱 목록 조회
- `POST /api/apps`: 앱 등록/수정
- `DELETE /api/apps/{app_code}`: 앱 삭제
- 설정 파일: `config/apps.json`

### 5.2 앱/트랙 메타
Job에 다음 메타가 저장됩니다.
- `app_code` (예: `mvp-1`)
- `track` (`new`, `enhance`, `bug`)

### 5.3 네이밍 분리
- 브랜치: `agenthub/{app_code}/issue-{number}-{jobid8}`
- 로그 파일: `{app_code}--{job_id}.log`
- 워크스페이스 경로: `workspaces/{app_code}/{owner__repo}`

---

## 6. 대시보드 UI 고도화 상태
### 6.1 메인
- 메뉴 분리: `리스트` / `설정`
- 리스트 실시간 갱신(`GET /api/jobs` polling)
- 상태 카드(전체/대기/실행/완료/실패)
- KST 포맷 시각 표시

### 6.2 설정 내부 메뉴 분리
- `이슈 등록`
- `앱 관리`
- `에이전트`
- 아이콘+라벨 메뉴 UI
- 모바일 반응형 대응

### 6.3 앱 관리 UI
- 등록 앱 목록 테이블(`앱명/코드/저장소/삭제`)
- 삭제 버튼 아이콘화(휴지통)

### 6.4 잡 상세
- 실시간 터미널 스타일 로그
- 최신 로그 상단 정렬
- 작업 단계/시도 그룹 하이라이트
- 행위자 라벨 표시 (`ORCHESTRATOR`, `CODER`, `PLANNER`, `REVIEWER`, `GITHUB`, `GIT`, `SYSTEM`, `SHELL`)
- 에러/경고 분류 요약

### 6.5 테마
- 다크/라이트 모드 토글
- 모드 상태 로컬 저장

---

## 7. 에이전트/명령 템플릿 관리
### 7.1 템플릿 설정 API
- `GET /api/agents/config`
- `POST /api/agents/config`

### 7.2 기능
- 쉬운 입력 모드 + 상세 문자열 모드
- Planner/Coder/Reviewer/Escalation 에이전트 분리 설정
- Escalation 활성화 토글 (`AGENTHUB_ENABLE_ESCALATION`)
- CLI 연결 확인: `GET /api/agents/check`
- 적용 모델 확인: `GET /api/agents/models`

### 7.3 현재 기본 커맨드 상태
- `planner/reviewer`: Gemini (`--model gemini-3.1-pro-preview` 고정)
- `coder`: Codex
- `escalation`: Claude (`--dangerously-skip-permissions`)
- 파일: `config/ai_commands.json`

---

## 8. 오류 분류/로그 정책
- `STDERR` 문자열 자체만으로 즉시 치명 오류 처리하지 않도록 완화
- 실제 실패 조건(종료코드/파이프라인 실패) 중심으로 최종 ERROR 판단
- 실패 원인 메시지에 다음 액션 가이드 포함

---

## 9. 운영/유틸 스크립트
- `scripts/setup_local_config.sh`
  - `.env`, `config/ai_commands.json`, `config/apps.json` 생성
- `scripts/install_systemd.sh`
  - API/Worker systemd 등록
- `scripts/test_live_webhook.sh`
  - 실환경 웹훅 플로우 검증
- `scripts/workspace_app.sh` (신규)
  - 앱 작업물 실행 관리
  - `start/stop/status`
  - 앱 코드별 포트 `3100~3199` 자동 할당/고정 (`config/app_ports.json`)

---

## 10. 주요 API 목록 요약
- `GET /` 대시보드
- `GET /api/jobs` 작업 리스트
- `GET /api/jobs/{job_id}` 작업 상세
- `GET /logs/{file_name}` 로그 파일
- `POST /webhooks/github` GitHub 웹훅
- `POST /api/issues/register` 대시보드 이슈 등록+트리거
- `GET /api/apps` 앱 목록
- `POST /api/apps` 앱 등록/수정
- `DELETE /api/apps/{app_code}` 앱 삭제
- `GET /api/agents/config` 템플릿 조회
- `POST /api/agents/config` 템플릿 저장
- `GET /api/agents/check` CLI 연결 확인
- `GET /api/agents/models` 모델 확인
- `GET /healthz` 헬스체크

---

## 11. 현재 운영 시 유의사항
- GitHub 이슈/PR 생성은 서버의 `gh auth`와 외부 네트워크(`api.github.com`) 상태에 의존
- npm 설치/외부 패키지 설치는 DNS/아웃바운드 정책에 영향받음
- 테스트용 앱 실행은 `workspace_app.sh`로 3100번대 포트 분리 권장

