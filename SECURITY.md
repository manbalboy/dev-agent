# Security Policy

## Supported Scope

현재는 `master` 브랜치의 최신 코드만 지원 대상으로 본다.

## Reporting a Vulnerability

보안 이슈는 공개 이슈로 바로 올리지 말고, 저장소 소유자에게 비공개로 먼저 전달한다.

보고 시 포함하면 좋은 정보:

- 영향 범위
- 재현 절차
- 관련 로그 또는 스크린샷
- 악용 가능성
- 제안하는 완화 방법

## Secret Handling

- `.env`, `.webhook_secret.txt`, API 키, 액세스 토큰은 저장소에 커밋하지 않는다.
- 시크릿이 커밋되었다고 의심되면 값을 즉시 폐기하고 새 값으로 교체한다.
- 커밋 히스토리에 남은 시크릿은 별도 운영 절차로 정리한다.
- 실제 절차는 [docs/SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md](./docs/SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md)를 따른다.

## Response Expectations

- 확인 응답: 가능한 한 빠르게
- 심각도 분류: 재현 후 결정
- 수정 및 공지: 영향도 기준으로 우선순위를 정해 진행

## Safe Defaults

- 운영 환경에서는 permissive CORS, 위험한 CLI 우회 플래그, 과도한 권한 설정을 기본값으로 사용하지 않는다.
- 위험 모드가 필요하면 명시적으로 opt-in 해야 한다.
