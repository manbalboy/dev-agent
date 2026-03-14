# Reverse Proxy TLS Runbook

기준 시각: 2026-03-14 (KST)

목적:
- AgentHub 운영 URL을 HTTPS 기준으로 고정하고 reverse proxy/LB 뒤에서 안전하게 배포하는 절차를 표준화한다.
- `security / TLS governance` 경고를 실제 운영 조치로 연결한다.

## 1. 적용 시점

- 신규 운영 서버를 처음 공개할 때
- 도메인/인증서/프록시 구성이 바뀔 때
- `Security / TLS Governance` 카드에서 아래 경고가 보일 때
  - `public_base_url_missing`
  - `public_base_url_not_https`
  - `https_not_enforced`
  - `forwarded_proto_not_trusted`
  - `cors_too_permissive`

## 2. 목표 상태

- 공개 URL은 `https://...`
- TLS 종료는 reverse proxy/LB 에서 수행
- 앱은 HTTP 직통 요청을 `426` 으로 거부
- 프록시가 `X-Forwarded-Proto: https` 를 전달
- CORS 는 운영 origin allow-list 만 허용

권장 `.env`:

```dotenv
AGENTHUB_PUBLIC_BASE_URL=https://agenthub.example.com
AGENTHUB_ENFORCE_HTTPS=true
AGENTHUB_TRUST_X_FORWARDED_PROTO=true
AGENTHUB_CORS_ALLOW_ALL=false
AGENTHUB_CORS_ORIGINS=https://agenthub.example.com,https://admin.agenthub.example.com
```

## 3. 사전 점검

1. 공개 도메인과 인증서 발급 주체를 확정한다.
2. reverse proxy/LB 가 `443 -> AgentHub API` 로 연결되는지 확인한다.
3. proxy 에서 아래 헤더를 넘길 수 있는지 확인한다.
   - `Host`
   - `X-Forwarded-Proto`
   - `X-Forwarded-For`
4. 대시보드 `Security / TLS Governance` 카드의 현재 경고를 기록한다.

## 4. reverse proxy 설정

예시 `nginx`:

```nginx
server {
    listen 443 ssl http2;
    server_name agenthub.example.com;

    ssl_certificate /etc/letsencrypt/live/agenthub.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agenthub.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8321;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
    }
}
```

원칙:
- AgentHub API 는 로컬 포트로만 bind 하고 외부 공개는 proxy 한 곳에서만 한다.
- HTTPS 리다이렉트는 proxy/LB 와 앱 둘 다 충돌 없이 동작하도록 구성한다.

## 5. AgentHub 적용 절차

1. 서버 `.env` 를 목표 상태 값으로 수정한다.
2. CORS origin 은 실제 운영 도메인만 남긴다.
3. 서비스 재기동:

```bash
sudo systemctl restart agenthub-api.service
sudo systemctl restart agenthub-worker.service
sudo systemctl restart agenthub-updater.service
sudo systemctl start agenthub-self-check.service
```

4. 대시보드 `Security / TLS Governance` 와 `Periodic Self-Check` 를 새로고침한다.

## 6. 검증 절차

1. 로컬 직통 요청은 차단되는지 확인

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8321/api/admin/security-governance
```

기대값:
- `426`

2. reverse proxy 헤더 경계 확인

```bash
curl -s \
  -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:8321/api/admin/security-governance
```

기대값:
- JSON payload
- `transport.https_enforced=true`

3. healthz 예외 확인

```bash
curl -s http://127.0.0.1:8321/healthz
```

기대값:
- `200`
- `{ "status": "ok" }`

4. 운영 surface 확인
- `Security / TLS Governance` 경고 수가 `0` 또는 예상 범위인지 확인
- `Periodic Self-Check` 에서 `security_warning_count` 가 내려갔는지 확인

## 7. 장애 시 임시 복구

증상:
- proxy 뒤 HTTPS 요청까지 `426` 으로 막힌다

점검 순서:
1. proxy 가 `X-Forwarded-Proto: https` 를 실제로 넘기는지 확인
2. `.env` 의 `AGENTHUB_TRUST_X_FORWARDED_PROTO=true` 적용 여부 확인
3. 서비스 재기동

정말 급한 임시 완화:
- 짧은 복구 시간 동안만 `AGENTHUB_ENFORCE_HTTPS=false` 로 낮출 수 있다.
- 원인 수정 후 즉시 다시 `true` 로 복구한다.

## 8. 운영 주기

- 인증서 만료 30일 전 갱신 확인
- 분기 1회 이상 `Security / TLS Governance` 와 `Periodic Self-Check` 점검
- 인프라 변경 직후 smoke test 재실행

## 9. 관련 문서

- [README.md](../README.md)
- [SECURITY.md](../SECURITY.md)
- [SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md](./SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md)
- [PRODUCTION_READINESS_TRIAGE_PLAN.md](./PRODUCTION_READINESS_TRIAGE_PLAN.md)
