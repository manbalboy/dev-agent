# Ultra-Long Test Safety Profile

기준 시각: 2026-03-13 (KST)

이 문서는 `초장기`, `ultra`, `ultra10` 잡을 실제 테스트 명령으로 검증할 때
권장하는 로컬 `.env` 안전 프로필을 정리한다.

중요:
- 이 값은 `로컬 운영 프로필`이다.
- 저장소 기본값을 바꾸는 문서가 아니다.
- `.env`는 로컬 파일이며 절대 커밋하지 않는다.

## 1. 목적

기본 quickstart는 빠른 부팅을 위해 테스트 명령을 `echo skip tests`로 시작할 수 있다.

하지만 `초장기` 시험에서는 아래가 필요하다.

- 실제 테스트 명령 실행
- stage 단위 timeout 확대
- 장시간 AI/테스트 단계 중 stale running 오탐 완화

## 2. 권장 로컬 `.env` 값

아래 값은 현재 저장소 기준으로 가장 안전한 baseline이다.

```dotenv
AGENTHUB_TEST_COMMAND="bash scripts/run_agenthub_tests.sh auto"
AGENTHUB_TEST_COMMAND_SECONDARY="bash scripts/run_agenthub_tests.sh auto"
AGENTHUB_TEST_COMMAND_IMPLEMENT="bash scripts/run_agenthub_tests.sh implement"
AGENTHUB_TEST_COMMAND_FIX="bash scripts/run_agenthub_tests.sh fix"
AGENTHUB_TEST_COMMAND_SECONDARY_IMPLEMENT="bash scripts/run_agenthub_tests.sh implement"
AGENTHUB_TEST_COMMAND_SECONDARY_FIX="bash scripts/run_agenthub_tests.sh fix"

AGENTHUB_TEST_COMMAND_TIMEOUT_SECONDS=3600
AGENTHUB_WORKER_STALE_RUNNING_SECONDS=7200
```

의미:
- `auto`: 프로젝트 타입에 맞는 테스트를 자동 감지한다.
- `implement` / `fix`: 구현 라운드와 수정 라운드에서도 실제 테스트 경로를 탄다.
- `3600`: 테스트 명령은 최대 1시간까지 허용한다.
- `7200`: worker stale running 판정은 2시간으로 늘린다.

## 3. 현재 저장소에서 확인한 값

현재 로컬 `.env`는 이미 위 프로필로 맞춘 상태다.

```dotenv
AGENTHUB_TEST_COMMAND="bash scripts/run_agenthub_tests.sh auto"
AGENTHUB_TEST_COMMAND_SECONDARY="bash scripts/run_agenthub_tests.sh auto"
AGENTHUB_TEST_COMMAND_IMPLEMENT="bash scripts/run_agenthub_tests.sh implement"
AGENTHUB_TEST_COMMAND_FIX="bash scripts/run_agenthub_tests.sh fix"
AGENTHUB_TEST_COMMAND_SECONDARY_IMPLEMENT="bash scripts/run_agenthub_tests.sh implement"
AGENTHUB_TEST_COMMAND_SECONDARY_FIX="bash scripts/run_agenthub_tests.sh fix"
AGENTHUB_WORKER_STALE_RUNNING_SECONDS=7200
AGENTHUB_TEST_COMMAND_TIMEOUT_SECONDS=3600
```

## 4. 검증 절차

초장기 시험 전에 최소 아래를 한 번 돌린다.

```bash
cd /home/docker/agentHub
bash scripts/run_agenthub_tests.sh auto
```

현재 저장소 기준 최근 검증 결과:
- `bash scripts/run_agenthub_tests.sh auto`
- 결과: `433 passed, 10 warnings`

필요하면 수정 라운드용 명령도 별도로 확인한다.

```bash
bash scripts/run_agenthub_tests.sh fix
```

## 5. 이 프로필이 보장하는 것

- `skip tests`가 아니라 실제 테스트 명령을 실행한다.
- 장시간 잡에서 테스트 timeout 때문에 너무 빨리 잘리는 상황을 줄인다.
- 단일 긴 단계 때문에 worker가 `stale running`으로 오탐하는 가능성을 낮춘다.

## 6. 이 프로필이 보장하지 않는 것

- 실제 애플리케이션 테스트가 항상 충분하다는 뜻은 아니다.
- 프로젝트가 테스트를 거의 갖고 있지 않으면 `auto`도 얕게 끝날 수 있다.
- `Codex/Gemini helper` 로그인/쿼터 문제는 별도다.
- patch/update updater flow와는 별개다.

## 7. 권장 사용 순서

1. 로컬 `.env`를 이 프로필로 맞춘다.
2. `bash scripts/run_agenthub_tests.sh auto`를 한 번 돌린다.
3. 그 다음 `초장기` 잡을 enqueue 한다.
4. 첫 실험은 운영 저장소보다 테스트 저장소에서 한다.
5. helper login/quota 경고는 본작업 실패와 분리해서 본다.

## 8. 관련 문서

- [README.md](../README.md)
- [CURRENT_HANDOFF.md](./CURRENT_HANDOFF.md)
- [PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md](./PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md)
