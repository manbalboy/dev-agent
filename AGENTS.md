# Local Agent Rules

이 저장소에서 작업하는 에이전트는 아래 규칙을 기본으로 따른다.

## 1. 종료 규칙

모든 작업은 기능 구현, 리팩터링, 문서 수정 여부와 관계없이 아래 둘 중 하나 이상으로 마무리한다.

1. 관련 문서 최신화
2. handoff 문서 업데이트

가능하면 둘 다 수행한다.

## 2. Handoff Rule

작업이 끝나면 반드시 [docs/CURRENT_HANDOFF.md](./docs/CURRENT_HANDOFF.md)를 갱신한다.

최소 포함 항목:

- 이번 턴에서 실제로 끝낸 것
- 현재 상태
- 다음 우선순위 1~3개
- 주의할 리스크/가정
- 검증 결과

## 3. Source Of Truth Rule

문서 간 충돌 시 아래 순서를 우선한다.

1. [README.md](./README.md)
2. [docs/DOCUMENT_MAP.md](./docs/DOCUMENT_MAP.md)
3. [docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md](./docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md)
4. 관련 phase 문서
5. [docs/CURRENT_HANDOFF.md](./docs/CURRENT_HANDOFF.md)

## 4. Small Slice Rule

- 한 번에 큰 구조를 바꾸지 않는다.
- 작은 단위로 나누고, 리팩터링은 기능 불변을 우선한다.
- 매 슬라이스마다 가능한 한 테스트를 추가하거나 기존 회귀로 닫는다.

## 5. Documentation Rule

- 코드 상태가 바뀌면 상위 계획 또는 해당 phase 문서 중 하나는 같이 갱신한다.
- 운영 절차는 설계 문서 대신 runbook 문서에 둔다.
- snapshot 문서는 큰 흐름만 유지하고, 최신 구현 세부를 중복해서 넣지 않는다.
