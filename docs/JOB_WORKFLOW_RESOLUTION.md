# Job Workflow Resolution

## 목적
한 job이 어떤 workflow를 사용할지 일관되게 결정한다.

## 우선순위
1. `job.workflow_id`
2. `app.workflow_id`
3. `default_workflow_id`
4. 최종 fallback: 고정 orchestrator pipeline

## 해석 규칙
- job에 명시된 `workflow_id`가 있으면 최우선으로 사용한다.
- job override가 없으면 앱 설정의 `workflow_id`를 사용한다.
- 앱 설정의 workflow가 현재 catalog에 없으면 default로 내린다.
- default workflow도 로딩/검증에 실패하면 기존 고정 파이프라인으로 fallback 한다.

## 현재 구현 범위
- `JobRecord.workflow_id` 저장
- webhook job 생성 시 `workflow:` 라벨 override 지원
- 대시보드 수동 job 생성 시 `workflow_id` override 지원
- orchestrator 실행 시 `job > app > default` 우선순위 적용
- resolution warning을 orchestrator log에 남김

## 현재 비범위
- workflow 조건식 기반 자동 선택
- failed node resume
- workflow version migration
- node-level retry policy
