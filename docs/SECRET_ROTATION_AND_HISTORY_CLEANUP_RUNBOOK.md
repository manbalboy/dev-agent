# Secret Rotation And History Cleanup Runbook

기준 시각: 2026-03-12 (KST)

목적:
- 이미 제거된 커밋형 시크릿의 운영 후속 절차를 표준화한다.
- 실제 시크릿 로테이션과 Git 히스토리 정리를 안전하게 수행하기 위한 체크리스트를 남긴다.

## 1. 대상

우선 점검 대상:

- `AGENTHUB_WEBHOOK_SECRET`
- `SEARCH_API_KEY`
- MCP/GitHub 관련 액세스 토큰
- `.env`, `.webhook_secret.txt`, 로컬 설정 파일에 들어 있을 수 있는 기타 자격 증명

## 2. 언제 수행해야 하나

- 시크릿이 저장소에 커밋되었거나 커밋되었을 가능성이 있을 때
- 로그/스크린샷/문서에 평문 노출이 발생했을 때
- 권한을 가진 사람이 바뀌었을 때
- 정기 보안 점검 주기에 맞춰 교체할 때

## 3. 즉시 대응

1. 해당 시크릿의 현재 사용처를 식별한다.
2. 새 시크릿을 발급한다.
3. 서버/CI/외부 서비스에 새 값을 먼저 배포한다.
4. 새 값으로 정상 동작을 확인한다.
5. 옛 값을 폐기한다.

주의:
- 코드 패치보다 실제 시크릿 폐기가 먼저다.
- 저장소에서 파일만 지워도 이미 노출된 값은 안전해지지 않는다.

## 4. AgentHub 기준 교체 절차

### 4.1 Webhook Secret

1. 새 랜덤 secret 생성
2. 서버 `.env`와 로컬 `.webhook_secret.txt` 갱신
3. API/worker 재시작
4. GitHub Webhook 설정에서 새 secret 반영
5. 테스트:

```bash
bash scripts/test_live_webhook.sh --issue <issue_number>
```

### 4.2 Search / MCP / 기타 API Key

1. 해당 서비스 콘솔에서 새 키 발급
2. 서버 환경 변수 갱신
3. 관련 기능 smoke test
4. 옛 키 폐기

## 5. Git History Cleanup

주의:
- 이 단계는 파괴적이다.
- 협업 저장소라면 반드시 공지 후 수행한다.
- 이미 clone 한 사용자는 rebase/reset 등 추가 조치가 필요하다.

권장 도구:
- `git filter-repo`
- 또는 `BFG Repo-Cleaner`

최소 원칙:
- 히스토리 정리 전 새 시크릿으로 교체 완료
- 정리 후 강제 push 계획과 팀 공지 준비
- GitHub secret scanning / 저장소 위생 검사로 재발 방지

## 6. Post-Check

- [ ] 새 시크릿이 실제로 동작한다
- [ ] 옛 시크릿이 폐기되었다
- [ ] `.env`, `.webhook_secret.txt`, `config/*.json` 같은 로컬 파일이 추적되지 않는다
- [ ] `python scripts/check_repo_hygiene.py` 통과
- [ ] 관련 운영 문서가 최신 상태다

## 7. Related Docs

- [README.md](../README.md)
- [SECURITY.md](../SECURITY.md)
- [PRODUCTION_READINESS_TRIAGE_PLAN.md](./PRODUCTION_READINESS_TRIAGE_PLAN.md)
- [DOCUMENT_MAP.md](./DOCUMENT_MAP.md)
