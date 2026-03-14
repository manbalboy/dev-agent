# Phase 7 Patch Control And Durable Runtime Plan

기준 시각: 2026-03-14 (KST)

## 1. 목적

- Phase 7의 목적은 `365일 운영 가능한 런타임`으로 가기 위한 운영 제어 계층을 만드는 것이다.
- 이 문서에서는 그중 첫 축을 `패치/업데이트 제어`로 정의한다.
- 목표는 운영자가 대시보드에서 아래 흐름을 안전하게 수행하는 것이다.
  - 새 패치가 있는지 확인
  - 패치를 승인
  - 서비스 drain / stop / update / restart 진행률 확인
  - 실패 시 상태와 복구 지점을 확인

## 2. 설계 원칙

- 항상 작은 슬라이스로 진행한다.
- `patch available` 감지부터 시작하고, 실제 자동 업데이트는 별도 updater 계층이 준비된 뒤에 연다.
- 현재 실행 중인 API/worker가 자기 자신을 직접 내리는 방식은 최종 구조로 보지 않는다.
- 진행률과 상태는 반드시 별도 state payload로 남겨 운영자가 UI에서 읽을 수 있어야 한다.
- 작업 종료 시에는 반드시 문서와 handoff를 같이 갱신한다.

## 3. 현재 상태

### DONE

- `7-A1 patch status detection baseline`
  - [app/patch_control_runtime.py](../app/patch_control_runtime.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/index.html](../app/templates/index.html)
  - 대시보드에서 현재 branch/commit/upstream/behind/ahead/dirty/update available을 읽을 수 있다.
  - `refresh=1`로 origin fetch 후 다시 비교할 수 있다.
  - update available일 때 operator-friendly 메시지(`패치가 있습니다. 진행하시겠습니까?`)를 반환한다.

- `7-A2 patch run state / progress`
  - [app/dashboard_patch_runtime.py](../app/dashboard_patch_runtime.py)
  - [app/store.py](../app/store.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/index.html](../app/templates/index.html)
  - `patch_runs` 저장소와 `PatchRunRecord`가 추가됐다.
  - 대시보드에서 최신 patch run 상태, 현재 단계, 진행률, 운영자 메모를 읽을 수 있다.
  - 현재 baseline 상태는 `waiting_updater`까지이며, 실제 서비스 stop/update/restart는 아직 하지 않는다.
  - 다음 액션은 `separate_updater_service_required`로 기록된다.

- `7-B1 separate updater service`
  - [app/patch_updater_runtime.py](../app/patch_updater_runtime.py)
  - [app/updater_main.py](../app/updater_main.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/index.html](../app/templates/index.html)
  - [systemd/agenthub-updater.service](../systemd/agenthub-updater.service)
  - [scripts/install_systemd.sh](../scripts/install_systemd.sh)
  - 별도 updater service가 `patch_updater_status.json` heartbeat를 기록한다.
  - `waiting_updater` patch run을 감지하면 claim 정보를 남기고 operator가 UI에서 확인할 수 있다.
  - 이번 baseline은 `claim + heartbeat + next action surface`까지이며, 실제 drain/update/restart는 아직 하지 않는다.

- `7-B2 service drain / stop / restart`
  - [app/patch_service_runtime.py](../app/patch_service_runtime.py)
  - [app/patch_updater_runtime.py](../app/patch_updater_runtime.py)
  - [app/updater_main.py](../app/updater_main.py)
  - [app/dashboard_job_action_runtime.py](../app/dashboard_job_action_runtime.py)
  - [app/github_webhook.py](../app/github_webhook.py)
  - updater가 `waiting_updater -> draining -> restarting` 상태 전이를 실제로 수행한다.
  - patch lock이 활성화되면 webhook, dashboard issue 등록, 주요 requeue action이 새 작업 수락을 막는다.
  - active job이 남아 있으면 drain 대기, 없으면 `worker stop -> api restart -> worker restart` 순서로 서비스 재기동을 수행한다.
  - patch lock은 restart 완료 또는 실패 시 해제된다.
  - 재기동 이후 다음 단계는 `7-C1 post-update health check`다.

- `7-C1 post-update health check`
  - [app/patch_health_runtime.py](../app/patch_health_runtime.py)
  - [app/patch_updater_runtime.py](../app/patch_updater_runtime.py)
  - [app/updater_main.py](../app/updater_main.py)
  - updater는 재기동 이후 `verifying` 단계에서 API `/healthz`, worker service active 상태, queue/store 접근, patch lock 해제 여부, updater status payload를 점검한다.
  - health check가 통과하면 patch run은 `done`으로 종료된다.
  - health check가 실패하면 patch run은 `failed`로 종료되고 `manual_post_update_check_required`를 남긴다.

- `7-C2 rollback baseline`
  - [app/patch_rollback_runtime.py](../app/patch_rollback_runtime.py)
  - [app/patch_updater_runtime.py](../app/patch_updater_runtime.py)
  - [app/updater_main.py](../app/updater_main.py)
  - [app/dashboard_patch_runtime.py](../app/dashboard_patch_runtime.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/index.html](../app/templates/index.html)
  - 실패한 patch run은 operator note와 함께 `rollback_requested`로 전이할 수 있다.
  - updater는 `rollback_requested -> rolling_back -> rollback_verifying -> rolled_back/rollback_failed`를 수행한다.
  - rollback은 dirty worktree를 거부하고, 저장된 `source_commit`으로 branch를 되돌린 뒤 서비스 재기동과 health check를 다시 수행한다.
  - rollback health check가 실패하면 `rollback_failed + manual_rollback_check_required`를 남긴다.

- `7-D1 backup baseline + patch coupling`
  - [app/patch_backup_runtime.py](../app/patch_backup_runtime.py)
  - [app/patch_updater_runtime.py](../app/patch_updater_runtime.py)
  - [app/updater_main.py](../app/updater_main.py)
  - [app/templates/index.html](../app/templates/index.html)
  - updater는 `draining` 종료 직후 서비스 재기동 전에 핵심 상태 파일을 `data/patch_backups/<backup_id>/`로 복사하고 `manifest.json`을 남긴다.
  - patch run details에는 `backup_manifest`가 같이 기록되어 operator가 backup id / 경로 / 파일 수를 UI에서 확인할 수 있다.
  - rollback 경로도 기존 backup이 없으면 destructive rollback 전에 같은 backup baseline을 먼저 만든다.
  - 이 backup manifest는 이제 dashboard/operator restore action의 source-of-truth로도 사용된다.

- `7-D2 restore action / backup verification`
  - [app/patch_backup_runtime.py](../app/patch_backup_runtime.py)
  - [app/dashboard_patch_runtime.py](../app/dashboard_patch_runtime.py)
  - [app/patch_updater_runtime.py](../app/patch_updater_runtime.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/index.html](../app/templates/index.html)
  - failed / rollback_failed / rolled_back / restore_failed patch run에서 backup manifest 검증 후 restore를 요청할 수 있다.
  - updater는 `restore_requested -> restoring -> restore_verifying -> restored / restore_failed`를 수행한다.
  - restore 이후 health check와 restore payload를 patch run details에 함께 남긴다.

- `7-E1 durable runtime / workspace hygiene baseline`
  - [app/durable_runtime_hygiene.py](../app/durable_runtime_hygiene.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/index.html](../app/templates/index.html)
  - [tests/test_durable_runtime_hygiene.py](../tests/test_durable_runtime_hygiene.py)
  - admin 화면에서 오래된 patch backup, invalid workspace backup, queue orphan/duplicate/stale entry, stale patch lock을 한 번에 점검할 수 있다.
  - cleanup은 live workspace를 자동 삭제하지 않고, retention 지난 terminal/orphan patch backup, invalid workspace backup, queue leftover, stale patch lock만 정리한다.
  - 최신 cleanup report는 `data/durable_runtime_hygiene_report.json`에 남고, 보존 기준은 `AGENTHUB_DURABLE_RETENTION_DAYS`(기본 `7`)다.

- `7-E2 security / TLS governance baseline`
  - [app/security_governance_runtime.py](../app/security_governance_runtime.py)
  - [app/main.py](../app/main.py)
  - [app/dashboard.py](../app/dashboard.py)
  - [app/templates/index.html](../app/templates/index.html)
  - [tests/test_security_governance_runtime.py](../tests/test_security_governance_runtime.py)
  - [tests/test_main_https_enforcement.py](../tests/test_main_https_enforcement.py)
  - admin 화면에서 `AGENTHUB_PUBLIC_BASE_URL`, `AGENTHUB_ENFORCE_HTTPS`, `AGENTHUB_TRUST_X_FORWARDED_PROTO`, CORS, webhook secret posture를 함께 점검할 수 있다.
  - `/healthz`를 제외한 경로는 `AGENTHUB_ENFORCE_HTTPS=true` 일 때 HTTP 요청을 `426 https_required`로 거부할 수 있다.
  - `.env.example`, `setup_local_config.sh`, repo hygiene check도 새 운영 경계를 같이 반영한다.

### CARRY-OVER TO PHASE 8

- `remaining runtime split / read-service long-tail`
- `durable backend` 경계 강화
- `self-check alert provider policy hardening`
- 위 항목들은 [PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md](./PHASE8_NONLINEAR_ENGINE_AND_SELF_GROWING_PLAN.md) 의 `8-E. Phase 7 Carry-Over Enabling Track`에서 계속 관리한다.

## 4. 단계별 계획

### 7-A. Detection And State

#### 7-A1. Patch Status Detection Baseline

- 목표:
  - 현재 배포 상태와 upstream 차이를 operator가 바로 읽게 한다.
- 완료 기준:
  - update available 여부가 admin 화면에 표시된다.
  - branch / commit / behind / ahead / dirty 상태가 반환된다.

#### 7-A2. Patch Run State Machine

- 목표:
  - 실제 패치 실행 전, patch run record와 진행률 payload를 도입한다.
- 범위:
  - `patch_runs` store
  - `queued / preparing / draining / updating / restarting / verifying / done / failed`
  - progress bar에 필요한 step index / total step / current message
- 완료 기준:
  - 아직 서비스 stop을 하지 않아도 patch run 상태를 UI에서 읽을 수 있다.

### 7-B. Updater Isolation

#### 7-B1. Separate Updater Service

- 목표:
  - 메인 API/worker와 분리된 updater 프로세스를 둔다.
- 이유:
  - 메인 서비스가 자기 자신을 내리면 진행률 surface가 같이 죽기 때문이다.
- 현재 상태:
  - baseline implemented
  - updater heartbeat/status 파일과 patch run claim surface까지 완료
  - 다음 단계는 실제 서비스 drain / stop / restart 연결

#### 7-B2. Service Drain / Stop / Restart

- 목표:
  - 새 job 수락 중지
  - worker drain
  - API/worker 재시작
- 완료 기준:
  - 패치 중 현재 상태가 patch run payload에 남는다.
  - patch lock이 webhook / dashboard enqueue / requeue 경로를 실제로 막는다.
  - active job drain 이후 worker stop / api restart / worker restart 순서가 실행된다.

### 7-C. Health And Rollback

#### 7-C1. Post-Update Health Check

- 목표:
  - API health / worker health / queue health 확인
- 현재 상태:
  - baseline implemented
  - updater가 재기동 후 상태 확인을 실행하고 `done / failed`로 patch run을 닫는다.
  - 다음 단계는 backup / restore coupling이다.

#### 7-C2. Rollback Baseline

- 목표:
  - 실패 시 직전 commit 또는 지정 release로 되돌리는 baseline 경로를 만든다.
- 현재 상태:
  - baseline implemented
  - failed patch run에 대해 operator-triggered rollback request를 남길 수 있다.
  - updater가 `source_commit` 기준 rollback, 서비스 재기동, rollback health check를 수행한다.
  - 다음 단계는 backup / restore coupling이다.

### 7-D. Durable Ops Coupling

#### 7-D1. Backup / Restore And Patch Coupling

- 목표:
  - patch 전후 backup/restore 절차를 runbook과 상태 기계에 연결한다.
- 현재 상태:
  - baseline implemented
  - patch 전 핵심 상태 backup manifest 생성과 patch run coupling까지 완료
  - 다음 단계는 operator restore action / backup verification이다.

#### 7-D2. Restore Action / Backup Verification

- 목표:
  - operator가 patch backup을 확인하고 필요 시 restore를 요청할 수 있게 한다.
  - restore 이후 health check와 audit trail을 patch run과 같은 shape로 남긴다.
- 현재 상태:
  - baseline implemented
  - dashboard에서 failed / rollback_failed / rolled_back patch run에 대해 restore를 요청할 수 있다.
  - updater는 `restore_requested -> restoring -> restore_verifying -> restored / restore_failed` 상태 전이를 수행한다.
  - restore 전 backup manifest를 검증하고, restore 후 health check까지 다시 기록한다.
  - 다음 단계는 durable runtime / workspace hygiene baseline 이후 periodic self-check와 운영 거버넌스 보강이다.

#### 7-E1. Durable Runtime / Workspace Hygiene Baseline

- 목표:
  - 장기 운영 중 쌓이는 workspace / queue / state 파일의 정합성을 점검하고 정리 기준을 만든다.
  - orphan/stale/leftover 상태를 patch/update 흐름과 분리해서 정기 관리할 수 있게 한다.
- 현재 상태:
  - baseline implemented
  - `Durable Runtime Hygiene` 카드와 `GET/POST /api/admin/durable-runtime-hygiene*` 경로가 추가됐다.
  - 오래된 terminal/orphan patch backup, invalid workspace backup, queue leftover, stale patch lock을 retention policy로 점검/정리할 수 있다.
  - live workspace는 unmanaged workspace로만 surface하고 자동 삭제하지 않는다.
  - 최신 cleanup report는 `data/durable_runtime_hygiene_report.json`에 남는다.
  - 다음 단계는 periodic self-check와 운영 거버넌스 / secret / TLS 보강이다.

#### 7-E2. Security / TLS Governance Baseline

- 목표:
  - 운영자가 현재 transport/CORS/secret posture를 UI에서 읽고, 최소한의 앱 레벨 HTTPS 강제 경계를 둘 수 있게 한다.
- 현재 상태:
  - baseline implemented
  - `Security / TLS Governance` 카드와 `GET /api/admin/security-governance` 경로가 추가됐다.
  - `AGENTHUB_PUBLIC_BASE_URL`, `AGENTHUB_ENFORCE_HTTPS`, `AGENTHUB_TRUST_X_FORWARDED_PROTO` 기준으로 공개 URL/TLS posture를 점검한다.
  - CORS allow-all/wildcard, test-like webhook secret, HTTPS 미강제 상태를 경고로 surface한다.
  - `AGENTHUB_ENFORCE_HTTPS=true` 일 때 `/healthz`를 제외한 HTTP 요청은 `426`으로 거부한다.
  - 다음 단계는 실제 secret rotation / reverse-proxy TLS 운영 절차 정례화와 dashboard write action/service 축소다.

#### 7-E3. Periodic Self-Check Baseline

- 목표:
  - patch/update, post-update health, hygiene, security posture를 정기 보고서 한 장으로 묶어 장기 운영 drift를 빨리 감지한다.
  - operator-triggered 점검을 넘어 systemd timer 기준의 정례 self-check baseline을 만든다.
- 현재 상태:
  - baseline implemented
  - `Periodic Self-Check` 카드와 `GET/POST /api/admin/durable-runtime-self-check*`, `POST /api/admin/durable-runtime-self-check/alert/acknowledge` 경로가 추가됐다.
  - [app/durable_runtime_self_check.py](../app/durable_runtime_self_check.py) 가 patch status, latest patch run/updater, post-update health, durable hygiene, security governance 상태를 한 payload로 합친다.
  - self-check warning set 기준으로 persisted alert lifecycle(`open` / `acknowledged` / `resolved`)를 함께 관리하고, 최신 보고서는 `data/durable_runtime_self_check_report.json`, 최신 alert state는 `data/durable_runtime_self_check_alert.json` 에 남는다.
  - [app/self_check_alert_delivery_runtime.py](../app/self_check_alert_delivery_runtime.py) 가 optional webhook delivery 상태를 별도 persisted payload로 관리하고, 최신 delivery state는 `data/durable_runtime_self_check_alert_delivery.json` 에 남는다.
  - webhook delivery 는 기본 `AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_URL`, critical 전용 `AGENTHUB_SELF_CHECK_ALERT_CRITICAL_WEBHOOK_URL`, `AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_TIMEOUT_SECONDS`(기본 `10`), `AGENTHUB_SELF_CHECK_ALERT_REPEAT_MINUTES`(기본 `180`), `AGENTHUB_SELF_CHECK_ALERT_FAILURE_BACKOFF_MAX_MINUTES`(기본 `720`)로 제어한다.
  - 같은 alert fingerprint는 route별 cooldown 이후에만 재전송하고, route 전송이 연속 실패하면 재시도 간격은 `repeat -> 2x -> 4x` 식의 exponential backoff를 적용하되 max backoff에서 상한을 둔다.
  - [app/self_check_main.py](../app/self_check_main.py) 가 one-shot entrypoint 로 추가됐고, systemd timer/manual run 이 같은 alert/report 파일을 갱신한다.
  - [systemd/agenthub-self-check.service](../systemd/agenthub-self-check.service) 와 [systemd/agenthub-self-check.timer](../systemd/agenthub-self-check.timer) 가 추가됐고, [scripts/install_systemd.sh](../scripts/install_systemd.sh) 는 timer enable + 초기 1회 실행까지 연결한다.
  - 기본 timer 주기는 `15min`, stale 기준은 `AGENTHUB_SELF_CHECK_STALE_MINUTES`(기본 `45`)다.
  - 현재 critical alert 는 추가 escalation route 로 보낼 수 있지만, 다음 단계는 durable backend 경계 보강과 provider별 재시도/disable policy다.

#### 7-E4. Secret Rotation / Reverse Proxy TLS Runbook Baseline

- 목표:
  - security posture 경고를 실제 운영 절차와 연결하고, secret rotation / TLS cutover 를 문서와 UI 기준으로 정례화한다.
- 현재 상태:
  - baseline implemented
  - [docs/SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md](../docs/SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md) 와 [docs/REVERSE_PROXY_TLS_RUNBOOK.md](../docs/REVERSE_PROXY_TLS_RUNBOOK.md) 를 운영 source-of-truth 로 연결했다.
  - [app/security_governance_runtime.py](../app/security_governance_runtime.py) 는 이제 runbook 경로, operator checklist, 권장 ENV payload 를 함께 반환한다.
  - dashboard `Security / TLS Governance` detail 은 runbook 경로와 checklist 를 같이 보여준다.
  - 다음 단계는 self-check alert automation 과 durable backend 경계 보강이다.

## 5. 현재 우선순위

- Phase 7 baseline은 사실상 닫혔고, 남은 blocker는 Phase 8 enabling track으로 이관했다.
1. `remaining runtime split / read-service long-tail 정리`
2. `durable backend / self-check alert provider policy hardening`
3. `LICENSE / 정책 의사결정`

## 6. 완료 판정

- 아래가 되기 전에는 `패치 제어가 됐다`고 보지 않는다.
  - update available 상태를 operator가 UI에서 본다.
  - patch run 상태와 진행률을 UI에서 본다.
  - 메인 서비스와 별도로 updater 상태가 유지된다.
  - 패치 후 health check와 실패 상태가 기록된다.
  - 실패 patch run을 operator 승인 후 rollback할 수 있다.
  - 패치 직전 backup manifest가 상태 기계와 UI에 함께 남는다.
  - operator가 backup manifest를 검증한 뒤 restore를 요청하고 결과를 UI에서 확인할 수 있다.
  - operator가 durable runtime hygiene 결과를 보고 safe cleanup을 실행할 수 있다.
  - operator가 security / TLS posture와 HTTPS 강제 상태를 UI에서 읽을 수 있다.
  - operator가 self-check alert state를 보고 acknowledge/resolved lifecycle을 UI에서 추적할 수 있다.
