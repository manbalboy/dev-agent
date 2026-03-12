# Contributing

## 기본 원칙

- 작은 단위로 변경한다.
- 기능 변경에는 회귀 테스트를 같이 넣는다.
- 사용자가 모르는 파괴적 변경은 하지 않는다.
- 시크릿과 운영 데이터는 커밋하지 않는다.

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 테스트

기본 회귀:

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

저장소 위생 검사:

```bash
python scripts/check_repo_hygiene.py
```

일부 타깃 테스트만 돌릴 때도 `PYTHONPATH=.`를 유지한다.

## 변경 방식

- UI 변경은 모바일 화면까지 같이 본다.
- 문서 변경은 현재 상태와 계획을 분리해서 적는다.
- 새 설정이나 플래그를 추가하면 기본값과 안전한 사용법을 같이 문서화한다.
- 작업이 끝나면 [docs/CURRENT_HANDOFF.md](./docs/CURRENT_HANDOFF.md)를 갱신한다.

## 보안

- `.env`
- `.webhook_secret.txt`
- 토큰, API 키, 자격 증명

위 항목은 저장소에 커밋하지 않는다.

취약점 제보는 [SECURITY.md](./SECURITY.md)를 따른다.
