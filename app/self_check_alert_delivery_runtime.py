"""Webhook delivery helpers for periodic durable runtime self-check alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse


class SelfCheckAlertDeliveryRuntime:
    """Persist and deliver self-check alerts to operator webhook routes."""

    def __init__(
        self,
        *,
        webhook_url: str,
        critical_webhook_url: str = "",
        delivery_file: Path,
        utc_now_iso: Callable[[], str],
        repeat_minutes: int = 180,
        failure_backoff_max_minutes: int = 720,
        timeout_seconds: int = 10,
        sender: Callable[[str, Dict[str, Any], int], Dict[str, Any]] | None = None,
    ) -> None:
        self.webhook_url = str(webhook_url or "").strip()
        self.critical_webhook_url = str(critical_webhook_url or "").strip()
        self.delivery_file = delivery_file
        self.utc_now_iso = utc_now_iso
        self.repeat_minutes = int(repeat_minutes)
        self.failure_backoff_max_minutes = max(int(failure_backoff_max_minutes), self.repeat_minutes)
        self.timeout_seconds = int(timeout_seconds)
        self.sender = sender or self._send_webhook_json

    def read_status(
        self,
        *,
        alert: Dict[str, Any] | None = None,
        report: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Return current delivery visibility without mutating state."""

        return self._build_status(
            alert=dict(alert or {}),
            report=dict(report or {}),
            deliver=False,
        )

    def process_alert(
        self,
        *,
        alert: Dict[str, Any],
        report: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Attempt webhook delivery when one alert is open and due."""

        payload = self._build_status(
            alert=dict(alert or {}),
            report=dict(report or {}),
            deliver=True,
        )
        self._write_json_atomic(self.delivery_file, payload)
        return payload

    def _build_status(
        self,
        *,
        alert: Dict[str, Any],
        report: Dict[str, Any],
        deliver: bool,
    ) -> Dict[str, Any]:
        saved = self._read_json_file(self.delivery_file)
        saved_routes = self._read_saved_routes(saved)
        route_specs = self._route_specs(alert)
        routes = [
            self._build_route_status(
                spec=spec,
                saved=saved_routes.get(str(spec["route_key"]), {}),
                alert=alert,
                report=report,
                deliver=deliver,
                route_count=len(route_specs),
            )
            for spec in route_specs
        ]
        return self._aggregate_payload(routes=routes, alert=alert)

    def _route_specs(self, alert: Dict[str, Any]) -> List[Dict[str, Any]]:
        active = bool(alert.get("active")) and str(alert.get("state") or "").strip() == "open"
        severity = str(alert.get("severity") or "").strip()
        specs: List[Dict[str, Any]] = []

        primary_target = self._sanitize_webhook_target(self.webhook_url)
        if self.webhook_url:
            specs.append(
                {
                    "route_key": "primary",
                    "label": "primary",
                    "webhook_url": self.webhook_url,
                    "webhook_target": primary_target,
                    "active": active,
                }
            )

        critical_target = self._sanitize_webhook_target(self.critical_webhook_url)
        if (
            self.critical_webhook_url
            and critical_target
            and critical_target != primary_target
        ):
            specs.append(
                {
                    "route_key": "critical_escalation",
                    "label": "critical escalation",
                    "webhook_url": self.critical_webhook_url,
                    "webhook_target": critical_target,
                    "active": active and severity == "critical",
                }
            )
        return specs

    def _build_route_status(
        self,
        *,
        spec: Dict[str, Any],
        saved: Dict[str, Any],
        alert: Dict[str, Any],
        report: Dict[str, Any],
        deliver: bool,
        route_count: int,
    ) -> Dict[str, Any]:
        payload = {
            **self._default_route_payload(),
            **saved,
            "route_key": str(spec.get("route_key") or "").strip(),
            "label": str(spec.get("label") or "").strip(),
            "configured": True,
            "webhook_target": str(spec.get("webhook_target") or "").strip(),
            "active": bool(spec.get("active")),
            "current_state": str(alert.get("state") or "idle").strip() or "idle",
            "current_fingerprint": str(alert.get("fingerprint") or "").strip(),
            "current_warning_count": int(alert.get("warning_count") or 0),
            "should_deliver": False,
            "message": "",
        }

        if not payload["active"]:
            payload.update(
                status="idle",
                last_reason="route_not_active",
                should_deliver=False,
                next_delivery_due_at="",
                message="이 route 는 현재 alert severity/state 기준 활성화되지 않았습니다.",
            )
            return payload

        decision = self._delivery_decision(
            saved=saved,
            current_fingerprint=str(payload["current_fingerprint"]),
            current_state=str(payload["current_state"]),
        )
        payload["should_deliver"] = bool(decision["should_deliver"])
        payload["last_reason"] = str(decision["reason"])
        payload["next_delivery_due_at"] = str(decision["next_delivery_due_at"])
        payload["effective_repeat_minutes"] = int(decision["effective_repeat_minutes"])
        payload["consecutive_failure_count"] = int(decision["consecutive_failure_count"])
        payload["backoff_active"] = bool(decision["backoff_active"])

        if not payload["should_deliver"]:
            previous_status = str(saved.get("last_status") or "").strip()
            payload.update(
                status="failed" if previous_status == "failed" else "cooldown",
                message=(
                    "최근 route 전송 실패가 누적돼 backoff 이후 재시도합니다."
                    if previous_status == "failed" and bool(payload["backoff_active"])
                    else "최근 route 전송이 실패해 cooldown 이후 재시도합니다."
                    if previous_status == "failed"
                    else "같은 alert 는 cooldown 이후에만 이 route 로 다시 전송합니다."
                ),
            )
            return payload

        if not deliver:
            payload.update(
                status="pending",
                message="이 route 로 self-check alert 전송이 필요합니다.",
            )
            return payload

        attempted_at = self.utc_now_iso()
        result = self._send_payload(
            route_key=str(payload["route_key"]),
            route_label=str(payload["label"]),
            url=str(spec.get("webhook_url") or "").strip(),
            alert=alert,
            report=report,
            sent_at=attempted_at,
            route_count=route_count,
        )
        success = bool(result.get("ok"))
        failure_count = 0 if success else int(saved.get("consecutive_failure_count") or 0) + 1
        effective_repeat_minutes = self.repeat_minutes if success else self._failure_repeat_minutes(failure_count)
        next_due = self._shift_minutes(attempted_at, effective_repeat_minutes)
        payload.update(
            status="sent" if success else "failed",
            last_status="sent" if success else "failed",
            last_attempt_at=attempted_at,
            last_sent_at=attempted_at if success else str(saved.get("last_sent_at") or ""),
            last_response_code=result.get("status_code"),
            last_error=str(result.get("error") or "").strip(),
            attempt_count=int(saved.get("attempt_count") or 0) + 1,
            sent_count=int(saved.get("sent_count") or 0) + (1 if success else 0),
            consecutive_failure_count=failure_count,
            effective_repeat_minutes=effective_repeat_minutes,
            backoff_active=(not success and effective_repeat_minutes > self.repeat_minutes),
            should_deliver=False,
            next_delivery_due_at=next_due,
            message=(
                f"{payload['label']} route 전송을 완료했습니다."
                if success
                else f"{payload['label']} route 전송이 실패했습니다. backoff 이후 재시도합니다."
            ),
        )
        return payload

    def _aggregate_payload(self, *, routes: List[Dict[str, Any]], alert: Dict[str, Any]) -> Dict[str, Any]:
        configured = bool(routes)
        active_routes = [item for item in routes if bool(item.get("active"))]
        latest_attempt = self._pick_latest_timestamp(active_routes, "last_attempt_at")
        latest_sent = self._pick_latest_timestamp(active_routes, "last_sent_at")
        next_due = self._pick_earliest_timestamp(active_routes, "next_delivery_due_at")
        failures = [item for item in active_routes if str(item.get("status") or "") == "failed"]
        pending = [item for item in active_routes if str(item.get("status") or "") == "pending"]
        sent = [item for item in active_routes if str(item.get("status") or "") == "sent"]
        cooldown = [item for item in active_routes if str(item.get("status") or "") == "cooldown"]
        any_should_deliver = any(bool(item.get("should_deliver")) for item in active_routes)
        effective_repeat_minutes = max(
            [int(item.get("effective_repeat_minutes") or 0) for item in active_routes] or [self.repeat_minutes]
        )
        consecutive_failure_count = max(
            [int(item.get("consecutive_failure_count") or 0) for item in active_routes] or [0]
        )
        backoff_active = any(bool(item.get("backoff_active")) for item in active_routes)

        if not configured:
            status = "disabled"
            message = "self-check alert webhook URL 이 설정되지 않았습니다."
            last_reason = "webhook_not_configured"
        elif not active_routes:
            status = "idle"
            message = "열린 self-check alert 가 없어 webhook 전송을 대기하지 않습니다."
            last_reason = "alert_not_open"
        elif pending:
            status = "pending"
            message = f"열린 self-check alert 를 {len(pending)}개 route 로 전송해야 합니다."
            last_reason = "pending_routes"
        elif failures and sent:
            status = "partial_failed"
            message = "일부 self-check alert route 전송이 실패했습니다."
            last_reason = "partial_route_failure"
        elif failures:
            status = "failed"
            message = "self-check alert route 전송이 실패했습니다."
            last_reason = "route_failure"
        elif sent and len(sent) == len(active_routes):
            status = "sent"
            message = f"self-check alert 를 {len(sent)}개 route 로 전송했습니다."
            last_reason = "sent"
        elif cooldown:
            status = "cooldown"
            message = "같은 self-check alert 는 cooldown 이후에만 다시 전송합니다."
            last_reason = "cooldown"
        else:
            status = "idle"
            message = "활성 route 가 없습니다."
            last_reason = "no_active_routes"

        failure_route = failures[0] if failures else {}
        primary_target = next(
            (str(item.get("webhook_target") or "") for item in routes if str(item.get("route_key") or "") == "primary"),
            "",
        )
        critical_target = next(
            (
                str(item.get("webhook_target") or "")
                for item in routes
                if str(item.get("route_key") or "") == "critical_escalation"
            ),
            "",
        )
        return {
            **self._default_payload(),
            "configured": configured,
            "webhook_target": primary_target,
            "critical_webhook_target": critical_target,
            "delivery_file": str(self.delivery_file),
            "repeat_minutes": self.repeat_minutes,
            "failure_backoff_max_minutes": self.failure_backoff_max_minutes,
            "timeout_seconds": self.timeout_seconds,
            "status": status,
            "message": message,
            "active": bool(alert.get("active")),
            "current_state": str(alert.get("state") or "idle").strip() or "idle",
            "current_fingerprint": str(alert.get("fingerprint") or "").strip(),
            "current_warning_count": int(alert.get("warning_count") or 0),
            "should_deliver": any_should_deliver,
            "next_delivery_due_at": next_due,
            "effective_repeat_minutes": effective_repeat_minutes,
            "consecutive_failure_count": consecutive_failure_count,
            "backoff_active": backoff_active,
            "last_status": status if status not in {"partial_failed"} else "failed",
            "last_reason": last_reason,
            "last_error": str(failure_route.get("last_error") or "").strip(),
            "last_response_code": failure_route.get("last_response_code"),
            "last_attempt_at": latest_attempt,
            "last_sent_at": latest_sent,
            "attempt_count": sum(int(item.get("attempt_count") or 0) for item in active_routes),
            "sent_count": sum(int(item.get("sent_count") or 0) for item in active_routes),
            "route_count": len(routes),
            "active_route_count": len(active_routes),
            "routed_targets": [
                str(item.get("webhook_target") or "").strip()
                for item in active_routes
                if str(item.get("webhook_target") or "").strip()
            ],
            "routes": routes,
        }

    def _delivery_decision(
        self,
        *,
        saved: Dict[str, Any],
        current_fingerprint: str,
        current_state: str,
    ) -> Dict[str, Any]:
        previous_fingerprint = str(saved.get("current_fingerprint") or "").strip()
        previous_state = str(saved.get("current_state") or "").strip()
        last_attempt_at = str(saved.get("last_attempt_at") or "").strip()
        last_status = str(saved.get("last_status") or "").strip()
        consecutive_failure_count = int(saved.get("consecutive_failure_count") or 0)

        if current_fingerprint != previous_fingerprint or current_state != previous_state:
            return {
                "should_deliver": True,
                "reason": "new_open_alert",
                "next_delivery_due_at": "",
                "effective_repeat_minutes": self.repeat_minutes,
                "consecutive_failure_count": 0,
                "backoff_active": False,
            }
        if not last_attempt_at:
            return {
                "should_deliver": True,
                "reason": "unsent_open_alert",
                "next_delivery_due_at": "",
                "effective_repeat_minutes": self.repeat_minutes,
                "consecutive_failure_count": 0,
                "backoff_active": False,
            }

        age_minutes = self._age_minutes(last_attempt_at)
        effective_repeat_minutes = (
            self._failure_repeat_minutes(max(consecutive_failure_count, 1))
            if last_status == "failed"
            else self.repeat_minutes
        )
        next_due = self._shift_minutes(last_attempt_at, effective_repeat_minutes)
        backoff_active = last_status == "failed" and effective_repeat_minutes > self.repeat_minutes
        normalized_failure_count = max(consecutive_failure_count, 1) if last_status == "failed" else 0
        if age_minutes is None or age_minutes >= effective_repeat_minutes:
            return {
                "should_deliver": True,
                "reason": "retry_due" if last_status == "failed" else "repeat_due",
                "next_delivery_due_at": next_due,
                "effective_repeat_minutes": effective_repeat_minutes,
                "consecutive_failure_count": normalized_failure_count,
                "backoff_active": backoff_active,
            }
        return {
            "should_deliver": False,
            "reason": "failed_backoff" if last_status == "failed" else "cooldown",
            "next_delivery_due_at": next_due,
            "effective_repeat_minutes": effective_repeat_minutes,
            "consecutive_failure_count": normalized_failure_count,
            "backoff_active": backoff_active,
        }

    def _failure_repeat_minutes(self, consecutive_failure_count: int) -> int:
        count = max(int(consecutive_failure_count), 1)
        scaled = self.repeat_minutes * (2 ** (count - 1))
        return min(scaled, self.failure_backoff_max_minutes)

    def _send_payload(
        self,
        *,
        route_key: str,
        route_label: str,
        url: str,
        alert: Dict[str, Any],
        report: Dict[str, Any],
        sent_at: str,
        route_count: int,
    ) -> Dict[str, Any]:
        payload = self._build_webhook_body(
            route_key=route_key,
            route_label=route_label,
            route_count=route_count,
            alert=alert,
            report=report,
            sent_at=sent_at,
        )
        try:
            response = self.sender(url, payload, self.timeout_seconds) or {}
        except Exception as exc:  # pragma: no cover
            return {
                "ok": False,
                "status_code": None,
                "error": str(exc),
            }
        return {
            "ok": bool(response.get("ok")),
            "status_code": response.get("status_code"),
            "error": str(response.get("error") or "").strip(),
        }

    def _build_webhook_body(
        self,
        *,
        route_key: str,
        route_label: str,
        route_count: int,
        alert: Dict[str, Any],
        report: Dict[str, Any],
        sent_at: str,
    ) -> Dict[str, Any]:
        report_meta = report.get("report_meta") if isinstance(report.get("report_meta"), dict) else {}
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        warnings = [item for item in (report.get("warnings") or []) if isinstance(item, dict)]
        return {
            "event": "durable_runtime_self_check_alert",
            "sent_at": sent_at,
            "delivery": {
                "route_key": route_key,
                "route_label": route_label,
                "route_count": route_count,
            },
            "alert": {
                "active": bool(alert.get("active")),
                "state": str(alert.get("state") or "").strip(),
                "severity": str(alert.get("severity") or "").strip(),
                "fingerprint": str(alert.get("fingerprint") or "").strip(),
                "warning_codes": list(alert.get("warning_codes") or []),
                "warning_count": int(alert.get("warning_count") or 0),
                "occurrence_count": int(alert.get("occurrence_count") or 0),
                "first_detected_at": str(alert.get("first_detected_at") or "").strip(),
                "last_detected_at": str(alert.get("last_detected_at") or "").strip(),
                "report_generated_at": str(alert.get("report_generated_at") or "").strip(),
            },
            "report": {
                "generated_at": str(report.get("generated_at") or "").strip(),
                "trigger": str(report.get("trigger") or "").strip(),
                "overall_status": str(report.get("overall_status") or "").strip(),
                "message": str(report.get("message") or "").strip(),
                "warning_count": int(report.get("warning_count") or len(warnings)),
                "warning_codes": [
                    str(item.get("code") or "").strip()
                    for item in warnings
                    if str(item.get("code") or "").strip()
                ],
                "failed_checks": list((report.get("patch_health") or {}).get("failed_checks") or []),
                "cleanup_candidate_count": int(summary.get("cleanup_candidate_count") or 0),
                "security_warning_count": int(summary.get("security_warning_count") or 0),
                "next_actions": list(report.get("next_actions") or [])[:5],
                "report_path": str(report_meta.get("path") or alert.get("report_path") or "").strip(),
            },
        }

    @staticmethod
    def _send_webhook_json(
        url: str,
        payload: Dict[str, Any],
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "agenthub-self-check/1.0",
                "X-AgentHub-Event": "durable-runtime-self-check-alert",
            },
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
                return {
                    "ok": 200 <= int(response.getcode() or 0) < 300,
                    "status_code": int(response.getcode() or 0),
                    "error": "",
                }
        except HTTPError as exc:
            return {
                "ok": False,
                "status_code": int(exc.code or 0),
                "error": str(exc),
            }
        except URLError as exc:
            return {
                "ok": False,
                "status_code": None,
                "error": str(exc.reason or exc),
            }
        except OSError as exc:
            return {
                "ok": False,
                "status_code": None,
                "error": str(exc),
            }

    @staticmethod
    def _sanitize_webhook_target(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return ""
        path = parsed.path or ""
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def _read_saved_routes(self, saved: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        routes = saved.get("routes")
        if isinstance(routes, list):
            items: Dict[str, Dict[str, Any]] = {}
            for item in routes:
                if not isinstance(item, dict):
                    continue
                route_key = str(item.get("route_key") or "").strip()
                if route_key:
                    items[route_key] = dict(item)
            if items:
                return items
        primary = {
            key: saved.get(key)
            for key in self._default_route_payload().keys()
            if key in saved
        }
        if primary:
            primary["route_key"] = "primary"
            primary["label"] = "primary"
            primary["configured"] = bool(saved.get("configured"))
            primary["webhook_target"] = str(saved.get("webhook_target") or "").strip()
            return {"primary": primary}
        return {}

    def _age_minutes(self, value: str) -> int | None:
        try:
            timestamp = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        now = datetime.fromisoformat(self.utc_now_iso())
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        delta = now - timestamp
        return max(int(delta.total_seconds() // 60), 0)

    @staticmethod
    def _shift_minutes(value: str, minutes: int) -> str:
        try:
            timestamp = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return ""
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return (timestamp + timedelta(minutes=int(minutes))).isoformat()

    @staticmethod
    def _pick_latest_timestamp(items: List[Dict[str, Any]], key: str) -> str:
        values = [str(item.get(key) or "").strip() for item in items if str(item.get(key) or "").strip()]
        if not values:
            return ""
        return max(values)

    @staticmethod
    def _pick_earliest_timestamp(items: List[Dict[str, Any]], key: str) -> str:
        values = [str(item.get(key) or "").strip() for item in items if str(item.get(key) or "").strip()]
        if not values:
            return ""
        return min(values)

    @staticmethod
    def _default_route_payload() -> Dict[str, Any]:
        return {
            "route_key": "",
            "label": "",
            "configured": False,
            "webhook_target": "",
            "active": False,
            "status": "disabled",
            "message": "",
            "current_state": "idle",
            "current_fingerprint": "",
            "current_warning_count": 0,
            "should_deliver": False,
            "next_delivery_due_at": "",
            "effective_repeat_minutes": 0,
            "consecutive_failure_count": 0,
            "backoff_active": False,
            "last_status": "",
            "last_reason": "",
            "last_error": "",
            "last_response_code": None,
            "last_attempt_at": "",
            "last_sent_at": "",
            "attempt_count": 0,
            "sent_count": 0,
        }

    @staticmethod
    def _default_payload() -> Dict[str, Any]:
        return {
            "configured": False,
            "webhook_target": "",
            "critical_webhook_target": "",
            "delivery_file": "",
            "repeat_minutes": 0,
            "failure_backoff_max_minutes": 0,
            "timeout_seconds": 0,
            "status": "disabled",
            "message": "",
            "active": False,
            "current_state": "idle",
            "current_fingerprint": "",
            "current_warning_count": 0,
            "should_deliver": False,
            "next_delivery_due_at": "",
            "effective_repeat_minutes": 0,
            "consecutive_failure_count": 0,
            "backoff_active": False,
            "last_status": "",
            "last_reason": "",
            "last_error": "",
            "last_response_code": None,
            "last_attempt_at": "",
            "last_sent_at": "",
            "attempt_count": 0,
            "sent_count": 0,
            "route_count": 0,
            "active_route_count": 0,
            "routed_targets": [],
            "routes": [],
        }

    @staticmethod
    def _read_json_file(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
