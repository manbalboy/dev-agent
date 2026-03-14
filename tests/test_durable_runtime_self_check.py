from __future__ import annotations

import json
from pathlib import Path

from app.durable_runtime_self_check import DurableRuntimeSelfCheckRuntime


def _build_runtime(
    tmp_path: Path,
    *,
    now: str = "2026-03-14T00:00:00+00:00",
    patch_status: dict | None = None,
    patch_run: dict | None = None,
    patch_updater: dict | None = None,
    patch_health: dict | None = None,
    hygiene: dict | None = None,
    security: dict | None = None,
    read_delivery=None,
    deliver=None,
) -> DurableRuntimeSelfCheckRuntime:
    return DurableRuntimeSelfCheckRuntime(
        build_patch_status=lambda: patch_status
        or {
            "status": "up_to_date",
            "message": "현재 배포 코드는 원격 기준 최신 상태입니다.",
            "update_available": False,
            "working_tree_dirty": False,
            "ahead_count": 0,
        },
        build_patch_run_payload=lambda: patch_run
        or {
            "active": False,
            "status": "idle",
            "message": "등록된 패치 실행 기록이 없습니다.",
        },
        build_patch_updater_status=lambda: patch_updater
        or {
            "status": "idle",
            "message": "Updater service가 연결되어 있으며 대기 중입니다.",
        },
        build_patch_health_payload=lambda: patch_health
        or {
            "ok": True,
            "failed_checks": [],
            "checks": {
                "api": {"ok": True},
                "worker": {"ok": True},
                "updater": {"ok": True},
            },
        },
        build_hygiene_status=lambda: hygiene
        or {
            "summary": {"cleanup_candidate_count": 0},
            "patch_lock": {"stale_active_lock": False},
        },
        build_security_status=lambda: security
        or {
            "warning_count": 0,
            "warnings": [],
            "next_actions": [],
        },
        utc_now_iso=lambda: now,
        report_file=tmp_path / "durable_runtime_self_check_report.json",
        alert_file=tmp_path / "durable_runtime_self_check_alert.json",
        delivery_file=tmp_path / "durable_runtime_self_check_alert_delivery.json",
        read_alert_delivery=read_delivery,
        deliver_alert=deliver,
        stale_after_minutes=45,
    )


def test_durable_runtime_self_check_runtime_persists_ready_report(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)

    payload = runtime.run_check(trigger="manual")

    assert payload["overall_status"] == "ready"
    assert payload["warning_count"] == 0
    assert payload["summary"]["patch_updater_status"] == "idle"
    assert payload["report_meta"]["exists"] is True
    saved = json.loads((tmp_path / "durable_runtime_self_check_report.json").read_text(encoding="utf-8"))
    assert saved["trigger"] == "manual"
    assert saved["overall_status"] == "ready"
    alert = json.loads((tmp_path / "durable_runtime_self_check_alert.json").read_text(encoding="utf-8"))
    assert alert["state"] == "idle"
    assert alert["active"] is False


def test_durable_runtime_self_check_runtime_aggregates_critical_warnings(tmp_path: Path) -> None:
    runtime = _build_runtime(
        tmp_path,
        patch_status={
            "status": "error",
            "message": "git status failed",
            "update_available": True,
            "working_tree_dirty": True,
            "ahead_count": 2,
        },
        patch_run={
            "active": False,
            "patch_run_id": "patch-1",
            "status": "failed",
            "message": "패치가 실패했습니다.",
        },
        patch_updater={
            "status": "offline",
            "message": "Updater service가 아직 시작되지 않았습니다.",
        },
        patch_health={
            "ok": False,
            "failed_checks": ["api", "worker"],
            "checks": {
                "api": {"message": "api down"},
                "worker": {"message": "worker inactive"},
            },
        },
        hygiene={
            "summary": {"cleanup_candidate_count": 3},
            "patch_lock": {"stale_active_lock": True},
        },
        security={
            "warning_count": 1,
            "warnings": [
                {
                    "code": "cors_too_permissive",
                    "severity": "high",
                    "message": "CORS 정책이 너무 넓습니다.",
                }
            ],
            "next_actions": ["CORS allow-list를 운영 origin 기준으로 줄입니다."],
        },
    )

    payload = runtime.run_check(trigger="systemd_timer")

    assert payload["overall_status"] == "critical"
    assert payload["summary"]["patch_health_failed_check_count"] == 2
    codes = {item["code"] for item in payload["warnings"]}
    assert "patch_status_error" in codes
    assert "patch_working_tree_dirty" in codes
    assert "patch_repository_ahead" in codes
    assert "patch_run_failed" in codes
    assert "patch_updater_offline" in codes
    assert "patch_health_api" in codes
    assert "durable_runtime_cleanup_candidates" in codes
    assert "durable_runtime_stale_patch_lock" in codes
    assert "cors_too_permissive" in codes
    assert payload["alert"]["active"] is True
    assert payload["alert"]["state"] == "open"
    assert payload["alert"]["occurrence_count"] == 1


def test_durable_runtime_self_check_runtime_marks_stale_saved_report(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path, now="2026-03-14T01:30:00+00:00")
    report_file = tmp_path / "durable_runtime_self_check_report.json"
    report_file.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-14T00:00:00+00:00",
                "trigger": "systemd_timer",
                "overall_status": "ready",
                "message": "periodic self-check 기준을 충족합니다.",
                "warnings": [],
                "next_actions": [],
                "summary": {"warning_count": 0},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = runtime.read_status()

    assert payload["overall_status"] == "warning"
    assert payload["report_meta"]["stale"] is True
    codes = {item["code"] for item in payload["warnings"]}
    assert "self_check_report_stale" in codes
    assert payload["alert"]["active"] is True
    assert payload["alert"]["state"] == "open"


def test_durable_runtime_self_check_runtime_acknowledges_active_alert(tmp_path: Path) -> None:
    runtime = _build_runtime(
        tmp_path,
        patch_updater={
            "status": "offline",
            "message": "Updater service가 아직 시작되지 않았습니다.",
        },
    )

    first_payload = runtime.run_check(trigger="systemd_timer")
    acknowledged = runtime.acknowledge_alert(acted_by="dashboard", note="operator acknowledged")
    next_payload = runtime.read_status()

    assert first_payload["alert"]["state"] == "open"
    assert acknowledged["alert"]["state"] == "acknowledged"
    assert acknowledged["alert"]["acknowledged"] is True
    assert acknowledged["alert"]["acknowledged_by"] == "dashboard"
    assert acknowledged["alert"]["note"] == "operator acknowledged"
    assert next_payload["alert"]["state"] == "acknowledged"
    assert next_payload["alert"]["acknowledged"] is True


def test_durable_runtime_self_check_runtime_resolves_acknowledged_alert_on_clean_run(tmp_path: Path) -> None:
    runtime = _build_runtime(
        tmp_path,
        now="2026-03-14T00:00:00+00:00",
        patch_updater={
            "status": "offline",
            "message": "Updater service가 아직 시작되지 않았습니다.",
        },
    )
    runtime.run_check(trigger="systemd_timer")
    runtime.acknowledge_alert(acted_by="dashboard", note="handled")

    resolved_runtime = _build_runtime(
        tmp_path,
        now="2026-03-14T00:15:00+00:00",
    )
    resolved = resolved_runtime.run_check(trigger="systemd_timer")

    assert resolved["overall_status"] == "ready"
    assert resolved["alert"]["active"] is False
    assert resolved["alert"]["state"] == "resolved"
    assert resolved["alert"]["resolved_at"] == "2026-03-14T00:15:00+00:00"
    assert resolved["alert"]["acknowledged_by"] == "dashboard"


def test_durable_runtime_self_check_runtime_attaches_delivery_payload(tmp_path: Path) -> None:
    observed = {}

    def read_delivery(alert: dict, report: dict) -> dict:
        observed["read_state"] = alert.get("state")
        return {"status": "cooldown", "configured": True}

    def deliver(alert: dict, report: dict) -> dict:
        observed["deliver_state"] = alert.get("state")
        observed["deliver_message"] = report.get("message")
        return {
            "status": "sent",
            "configured": True,
            "attempt_count": 1,
            "sent_count": 1,
        }

    runtime = _build_runtime(
        tmp_path,
        patch_updater={
            "status": "offline",
            "message": "Updater service가 아직 시작되지 않았습니다.",
        },
        read_delivery=read_delivery,
        deliver=deliver,
    )

    payload = runtime.run_check(trigger="manual")
    status = runtime.read_status()

    assert payload["delivery"]["status"] == "sent"
    assert payload["delivery"]["sent_count"] == 1
    assert observed["deliver_state"] == "open"
    assert "경고" in observed["deliver_message"]
    assert status["delivery"]["status"] == "cooldown"
    assert observed["read_state"] == "open"
