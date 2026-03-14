from __future__ import annotations

import json
from pathlib import Path

from app.self_check_alert_delivery_runtime import SelfCheckAlertDeliveryRuntime


def _make_alert(**overrides):
    payload = {
        "active": True,
        "state": "open",
        "severity": "critical",
        "fingerprint": "patch_updater_offline|patch_health_api",
        "warning_codes": ["patch_updater_offline", "patch_health_api"],
        "warning_count": 2,
        "occurrence_count": 1,
        "first_detected_at": "2026-03-14T00:00:00+00:00",
        "last_detected_at": "2026-03-14T00:00:00+00:00",
        "report_generated_at": "2026-03-14T00:00:00+00:00",
        "report_path": "data/durable_runtime_self_check_report.json",
    }
    payload.update(overrides)
    return payload


def _make_report(**overrides):
    payload = {
        "generated_at": "2026-03-14T00:00:00+00:00",
        "trigger": "systemd_timer",
        "overall_status": "critical",
        "message": "periodic self-check 경고 2건이 있습니다.",
        "warning_count": 2,
        "warnings": [
            {"code": "patch_updater_offline", "severity": "high", "message": "offline"},
            {"code": "patch_health_api", "severity": "high", "message": "api down"},
        ],
        "next_actions": ["서비스 상태를 확인합니다."],
        "summary": {
            "cleanup_candidate_count": 1,
            "security_warning_count": 0,
        },
        "patch_health": {"failed_checks": ["api"]},
        "report_meta": {"path": "data/durable_runtime_self_check_report.json"},
    }
    payload.update(overrides)
    return payload


def test_self_check_alert_delivery_runtime_reports_disabled_without_webhook(tmp_path: Path) -> None:
    runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="",
        delivery_file=tmp_path / "delivery.json",
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
    )

    payload = runtime.read_status(alert=_make_alert(), report=_make_report())

    assert payload["configured"] is False
    assert payload["status"] == "disabled"
    assert payload["should_deliver"] is False
    assert payload["last_reason"] == "webhook_not_configured"
    assert payload["route_count"] == 0


def test_self_check_alert_delivery_runtime_sends_new_open_alert(tmp_path: Path) -> None:
    observed = {}

    def sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        observed["url"] = url
        observed["payload"] = payload
        observed["timeout_seconds"] = timeout_seconds
        return {"ok": True, "status_code": 204}

    runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check?token=secret",
        delivery_file=tmp_path / "delivery.json",
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        repeat_minutes=180,
        timeout_seconds=12,
        sender=sender,
    )

    payload = runtime.process_alert(alert=_make_alert(), report=_make_report())

    assert payload["status"] == "sent"
    assert payload["last_status"] == "sent"
    assert payload["attempt_count"] == 1
    assert payload["sent_count"] == 1
    assert payload["effective_repeat_minutes"] == 180
    assert payload["consecutive_failure_count"] == 0
    assert payload["backoff_active"] is False
    assert payload["route_count"] == 1
    assert payload["active_route_count"] == 1
    assert payload["webhook_target"] == "https://hooks.example.com/agenthub/self-check"
    assert observed["url"] == "https://hooks.example.com/agenthub/self-check?token=secret"
    assert observed["timeout_seconds"] == 12
    assert observed["payload"]["event"] == "durable_runtime_self_check_alert"
    assert observed["payload"]["alert"]["fingerprint"] == _make_alert()["fingerprint"]
    saved = json.loads((tmp_path / "delivery.json").read_text(encoding="utf-8"))
    assert saved["status"] == "sent"
    assert saved["sent_count"] == 1
    assert len(saved["routes"]) == 1


def test_self_check_alert_delivery_runtime_respects_cooldown_for_same_alert(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        attempts["count"] += 1
        return {"ok": True, "status_code": 204}

    delivery_file = tmp_path / "delivery.json"
    first_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        delivery_file=delivery_file,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        repeat_minutes=180,
        sender=sender,
    )
    first_runtime.process_alert(alert=_make_alert(), report=_make_report())

    second_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        delivery_file=delivery_file,
        utc_now_iso=lambda: "2026-03-14T00:30:00+00:00",
        repeat_minutes=180,
        sender=sender,
    )
    payload = second_runtime.process_alert(alert=_make_alert(), report=_make_report())

    assert attempts["count"] == 1
    assert payload["status"] == "cooldown"
    assert payload["should_deliver"] is False
    assert payload["next_delivery_due_at"] == "2026-03-14T03:00:00+00:00"
    assert payload["effective_repeat_minutes"] == 180
    assert payload["consecutive_failure_count"] == 0


def test_self_check_alert_delivery_runtime_retries_failed_send_after_cooldown(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def failing_sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        attempts["count"] += 1
        return {"ok": False, "status_code": 500, "error": "server error"}

    delivery_file = tmp_path / "delivery.json"
    failed_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        delivery_file=delivery_file,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        repeat_minutes=180,
        sender=failing_sender,
    )
    failed = failed_runtime.process_alert(alert=_make_alert(), report=_make_report())

    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1
    assert failed["sent_count"] == 0
    assert failed["effective_repeat_minutes"] == 180
    assert failed["consecutive_failure_count"] == 1
    assert failed["last_response_code"] == 500

    def success_sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        attempts["count"] += 1
        return {"ok": True, "status_code": 204}

    retry_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        delivery_file=delivery_file,
        utc_now_iso=lambda: "2026-03-14T03:10:00+00:00",
        repeat_minutes=180,
        sender=success_sender,
    )
    retried = retry_runtime.process_alert(alert=_make_alert(), report=_make_report())

    assert attempts["count"] == 2
    assert retried["status"] == "sent"
    assert retried["attempt_count"] == 2
    assert retried["sent_count"] == 1
    assert retried["effective_repeat_minutes"] == 180
    assert retried["consecutive_failure_count"] == 0
    assert retried["backoff_active"] is False


def test_self_check_alert_delivery_runtime_applies_exponential_backoff_to_repeated_failures(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def failing_sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        attempts["count"] += 1
        return {"ok": False, "status_code": 503, "error": "service unavailable"}

    delivery_file = tmp_path / "delivery.json"
    first_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        delivery_file=delivery_file,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        repeat_minutes=180,
        failure_backoff_max_minutes=720,
        sender=failing_sender,
    )
    first_failed = first_runtime.process_alert(alert=_make_alert(), report=_make_report())

    second_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        delivery_file=delivery_file,
        utc_now_iso=lambda: "2026-03-14T03:10:00+00:00",
        repeat_minutes=180,
        failure_backoff_max_minutes=720,
        sender=failing_sender,
    )
    second_failed = second_runtime.process_alert(alert=_make_alert(), report=_make_report())

    cooldown_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        delivery_file=delivery_file,
        utc_now_iso=lambda: "2026-03-14T06:00:00+00:00",
        repeat_minutes=180,
        failure_backoff_max_minutes=720,
        sender=failing_sender,
    )
    cooldown = cooldown_runtime.process_alert(alert=_make_alert(), report=_make_report())

    assert attempts["count"] == 2
    assert first_failed["effective_repeat_minutes"] == 180
    assert first_failed["consecutive_failure_count"] == 1
    assert second_failed["effective_repeat_minutes"] == 360
    assert second_failed["consecutive_failure_count"] == 2
    assert second_failed["backoff_active"] is True
    assert cooldown["status"] == "failed"
    assert cooldown["should_deliver"] is False
    assert cooldown["effective_repeat_minutes"] == 360
    assert cooldown["consecutive_failure_count"] == 2
    assert cooldown["backoff_active"] is True
    assert cooldown["next_delivery_due_at"] == "2026-03-14T09:10:00+00:00"


def test_self_check_alert_delivery_runtime_routes_critical_alert_to_escalation_target(tmp_path: Path) -> None:
    observed = []

    def sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        observed.append((url, payload["delivery"]["route_key"]))
        return {"ok": True, "status_code": 204}

    runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        critical_webhook_url="https://hooks.example.com/agenthub/self-check-critical",
        delivery_file=tmp_path / "delivery.json",
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        repeat_minutes=180,
        sender=sender,
    )

    payload = runtime.process_alert(alert=_make_alert(severity="critical"), report=_make_report())

    assert payload["status"] == "sent"
    assert payload["route_count"] == 2
    assert payload["active_route_count"] == 2
    assert payload["sent_count"] == 2
    assert payload["critical_webhook_target"] == "https://hooks.example.com/agenthub/self-check-critical"
    assert {item[1] for item in observed} == {"primary", "critical_escalation"}
    assert len(payload["routes"]) == 2
    assert {item["route_key"] for item in payload["routes"]} == {"primary", "critical_escalation"}


def test_self_check_alert_delivery_runtime_keeps_critical_route_idle_for_warning_alert(tmp_path: Path) -> None:
    observed = []

    def sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        observed.append(payload["delivery"]["route_key"])
        return {"ok": True, "status_code": 204}

    runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        critical_webhook_url="https://hooks.example.com/agenthub/self-check-critical",
        delivery_file=tmp_path / "delivery.json",
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        repeat_minutes=180,
        sender=sender,
    )

    payload = runtime.process_alert(alert=_make_alert(severity="warning"), report=_make_report(overall_status="warning"))

    assert payload["status"] == "sent"
    assert payload["route_count"] == 2
    assert payload["active_route_count"] == 1
    assert payload["sent_count"] == 1
    assert observed == ["primary"]
    critical_route = next(item for item in payload["routes"] if item["route_key"] == "critical_escalation")
    assert critical_route["status"] == "idle"
    assert critical_route["active"] is False


def test_self_check_alert_delivery_runtime_suppresses_acknowledged_alert_delivery(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def sender(url: str, payload: dict, timeout_seconds: int) -> dict:
        attempts["count"] += 1
        return {"ok": True, "status_code": 204}

    runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url="https://hooks.example.com/agenthub/self-check",
        critical_webhook_url="https://hooks.example.com/agenthub/self-check-critical",
        delivery_file=tmp_path / "delivery.json",
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        repeat_minutes=180,
        sender=sender,
    )

    payload = runtime.process_alert(
        alert=_make_alert(state="acknowledged", acknowledged=True),
        report=_make_report(),
    )

    assert attempts["count"] == 0
    assert payload["status"] == "idle"
    assert payload["active_route_count"] == 0
    assert payload["should_deliver"] is False
