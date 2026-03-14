# Production Readiness Triage Plan

기준 시각: 2026-03-12 (KST)

## 목적

외부 심층 분석 보고서의 지적 사항을 현재 저장소 기준으로 다시 검토하고, 실제로 남아 있는 항목만 우선순위화한다.

원칙:

- 운영 위험이 큰 항목부터 처리한다.
- 작은 단위로 나눠서 반영한다.
- 기능 리팩터보다 보안/문서/CI 같은 저위험 기반 작업을 먼저 닫는다.
- 각 슬라이스마다 회귀 테스트를 돌린다.

## 재분류 결과

### 현재도 유효한 항목

1. `P1` 라이선스 정책 미정
   - `LICENSE`는 아직 없다.
   - 기술 작업이 아니라 저장소 정책 결정이 필요하다.

2. `P1` 운영 시크릿 후속 작업
   - 코드상 추적 제거와 ignore는 끝났지만, 실제 webhook/search/MCP 키 로테이션은 운영 작업으로 남아 있다.
   - admin 화면의 `security / TLS governance` baseline과 app-level HTTPS 강제 경계는 들어갔지만, 실제 secret/TLS 운영 절차는 여전히 남아 있다.
   - 필요 시 Git 히스토리 정리도 별도 절차로 진행해야 한다.

3. `P2` 문서 싱크 유지
   - 빠르게 기능을 붙이면서 일부 상위/요약 문서가 뒤처질 수 있다.
   - README, 상위 계획, phase 문서를 우선 source-of-truth로 유지해야 한다.

### 일부는 이미 완화되었거나 보고서가 뒤처진 항목

1. 워크플로우/메모리/운영 문서 부재
   - 현재는 `docs/` 아래에 Phase 1~5, 운영 원칙, 로드맵 문서가 이미 존재한다.

2. 테스트 기반 부족
   - 현재 저장소는 회귀 테스트 범위가 꽤 넓고, 최근 변경까지 테스트로 닫고 있다.
   - 문제는 테스트가 없는 것이 아니라 자동 실행 CI가 없는 점이다.

3. 대형 파일 분리 필요
   - 여전히 유효한 방향이지만, 최근 `shell_test_runtime`, `recovery_runtime`, `tool_runtime`처럼 일부 분리가 이미 시작된 상태다.
   - 즉시 P0/P1로 다룰 항목은 아니다.

4. 보안/협업 기본 문서 부재
   - `SECURITY.md`, `CONTRIBUTING.md`, minimal CI, Dependabot, 저장소 위생 검사는 이미 반영됐다.

5. 예시 설정의 위험 플래그 기본값
   - `config/ai_commands.example.json`은 안전 모드로 바뀌었고, `scripts/setup_local_config.sh`는 `--danger-mode` opt-in으로 바뀌었다.
   - 대시보드도 현재 템플릿의 `DANGER/SAFE` 상태와 위험 템플릿 키를 보여주며, 원클릭 제거 보조가 들어갔다.

### 별도 의사결정이 필요한 항목

1. `LICENSE`
   - 기술 작업이 아니라 프로젝트 정책 결정이다.
   - 저장소 소유자가 라이선스 정책을 정한 뒤 추가하는 것이 맞다.

2. 시크릿 히스토리 정리
   - `git filter-repo`나 BFG는 파괴적 작업이다.
   - 로컬 패치로 자동 수행하지 않고 운영 런북으로 남긴다.

3. AI 실행 템플릿의 위험 플래그 기본값
   - 예시/설정 생성 기본값은 이미 opt-in으로 전환됐다.
   - 다만 각 운영 환경의 실제 로컬 `config/ai_commands.json`에는 과거 설정이 남아 있을 수 있어 수동 정리와 확인이 필요하다.

## 실행 순서

### Slice 1

상태: `DONE`

- `.webhook_secret.txt` 추적 해제
- `.gitignore`에 로컬 시크릿 파일 추가
- `SECURITY.md` 추가
- `.env.example` 기본 CORS 값 제한

### Slice 2

상태: `DONE`

- `.github/workflows/ci.yml` 추가
- `CONTRIBUTING.md` 추가
- README에 보안/기여 흐름 연결

### Slice 3

상태: `PARTIAL`

- `LICENSE` 정책 확정 후 반영
- Dependabot 도입
- 위험 플래그 기본값 opt-in 전환
- 저장소 위생 검사와 협업 템플릿 추가

이미 완료된 항목:
- Dependabot 도입
- 위험 플래그 기본값 opt-in 전환
- 저장소 위생 검사와 협업 템플릿 추가

현재 남은 항목:
- `LICENSE`
- 실제 운영 시크릿 로테이션 / 필요 시 히스토리 정리
- 오래된 요약 문서 지속 동기화

## 운영 후속 작업

코드 변경만으로 끝나지 않는 항목:

1. 실제 GitHub webhook secret 로테이션
2. 검색 API 키, MCP 토큰 등 운영 시크릿 전수 점검
3. reverse proxy / TLS termination 실제 점검
4. 필요 시 저장소 히스토리에서 시크릿 흔적 제거

참고 runbook:
- [SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md](./SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md)
- [REVERSE_PROXY_TLS_RUNBOOK.md](./REVERSE_PROXY_TLS_RUNBOOK.md)

## 완료 기준

- 저장소 루트에 시크릿 파일이 더 이상 추적되지 않는다.
- 보안 제보와 기여 절차가 문서로 존재한다.
- GitHub에서 최소 `pytest` CI가 자동 실행된다.
- 예시 설정이 기본적으로 allow-all이 아니다.
