# AgentHub Dashboard Next

React/Next 기반 대시보드 프로토타입입니다.
기존 AgentHub API(`:8321`)를 호출합니다.

## 실행

```bash
cd /home/docker/agentHub/dashboard-next
npm install
npm run dev
```

기본 포트: `3100`

## API 연결

기본 동작:
- 브라우저는 항상 Next 상대경로(`/api/*`)로 호출
- Next 서버가 내부 프록시로 AgentHub API에 전달

선택 설정:
- `AGENTHUB_API_BASE`를 지정하면 프록시 대상 변경
- 미지정 시 기본값: `http://127.0.0.1:8321`

예시:
```bash
AGENTHUB_API_BASE="http://127.0.0.1:8321" npm run dev
```
