"""Periodic durable runtime self-check aggregation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Dict, List


class DurableRuntimeSelfCheckRuntime:
    """Aggregate operator-facing runtime checks into one persisted report."""

    def __init__(
        self,
        *,
        build_patch_status: Callable[[], Dict[str, Any]],
        build_patch_run_payload: Callable[[], Dict[str, Any]],
        build_patch_updater_status: Callable[[], Dict[str, Any]],
        build_patch_health_payload: Callable[[], Dict[str, Any]],
        build_hygiene_status: Callable[[], Dict[str, Any]],
        build_security_status: Callable[[], Dict[str, Any]],
        utc_now_iso: Callable[[], str],
        report_file: Path,
        alert_file: Path,
        delivery_file: Path | None = None,
        read_alert_delivery: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]] | None = None,
        deliver_alert: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]] | None = None,
        stale_after_minutes: int = 45,
    ) -> None:
        self.build_patch_status = build_patch_status
        self.build_patch_run_payload = build_patch_run_payload
        self.build_patch_updater_status = build_patch_updater_status
        self.build_patch_health_payload = build_patch_health_payload
        self.build_hygiene_status = build_hygiene_status
        self.build_security_status = build_security_status
        self.utc_now_iso = utc_now_iso
        self.report_file = report_file
        self.alert_file = alert_file
        self.delivery_file = delivery_file
        self.read_alert_delivery = read_alert_delivery
        self.deliver_alert = deliver_alert
        self.stale_after_minutes = int(stale_after_minutes)

    def read_status(self) -> Dict[str, Any]:
        """Return the latest persisted self-check payload for dashboard visibility."""

        if not self.report_file.exists():
            return self._decorate_payload(
                {
                    "generated_at": "",
                    "trigger": "",
                    "overall_status": "warning",
                    "message": "periodic self-check 보고서가 아직 없습니다.",
                    "warnings": [
                        {
                            "code": "self_check_report_missing",
                            "severity": "medium",
                            "source": "self_check_report",
                            "message": "periodic self-check 보고서가 아직 없습니다.",
                        }
                    ],
                    "next_actions": [
                        "`agenthub-self-check.service` 를 한 번 실행하거나 timer 상태를 확인합니다.",
                    ],
                    "summary": self._empty_summary(),
                    "patch_status": {},
                    "patch_run": {},
                    "patch_updater": {},
                    "patch_health": {},
                    "durable_runtime_hygiene": {},
                    "security_governance": {},
                    "docs": self._docs_payload(),
                },
                report_exists=False,
            )
        try:
            payload = json.loads(self.report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self._decorate_payload(
                {
                    "generated_at": "",
                    "trigger": "",
                    "overall_status": "critical",
                    "message": f"periodic self-check 보고서를 읽을 수 없습니다: {exc}",
                    "warnings": [
                        {
                            "code": "self_check_report_read_error",
                            "severity": "high",
                            "source": "self_check_report",
                            "message": f"periodic self-check 보고서를 읽을 수 없습니다: {exc}",
                        }
                    ],
                    "next_actions": [
                        "보고서 파일과 `agenthub-self-check.service` 실행 로그를 확인합니다.",
                    ],
                    "summary": self._empty_summary(),
                    "patch_status": {},
                    "patch_run": {},
                    "patch_updater": {},
                    "patch_health": {},
                    "durable_runtime_hygiene": {},
                    "security_governance": {},
                    "docs": self._docs_payload(),
                },
                report_exists=True,
            )
        if not isinstance(payload, dict):
            return self._decorate_payload(
                {
                    "generated_at": "",
                    "trigger": "",
                    "overall_status": "critical",
                    "message": "periodic self-check 보고서 형식이 올바르지 않습니다.",
                    "warnings": [
                        {
                            "code": "self_check_report_invalid",
                            "severity": "high",
                            "source": "self_check_report",
                            "message": "periodic self-check 보고서 형식이 올바르지 않습니다.",
                        }
                    ],
                    "next_actions": [
                        "보고서 파일을 다시 생성하도록 `agenthub-self-check.service` 를 재실행합니다.",
                    ],
                    "summary": self._empty_summary(),
                    "patch_status": {},
                    "patch_run": {},
                    "patch_updater": {},
                    "patch_health": {},
                    "durable_runtime_hygiene": {},
                    "security_governance": {},
                    "docs": self._docs_payload(),
                },
                report_exists=True,
            )
        return self._decorate_payload(dict(payload), report_exists=True)

    def run_check(self, *, trigger: str = "manual") -> Dict[str, Any]:
        """Execute one self-check pass, persist the report, and return it."""

        payload = self._build_payload(trigger=trigger)
        self._write_json_atomic(self.report_file, payload)
        decorated = self._decorate_payload(payload, report_exists=True)
        alert_payload = self._update_alert_state(decorated)
        delivery_payload = self._alert_delivery_payload(
            alert_payload=alert_payload,
            report_payload=decorated,
            deliver=True,
        )
        return {
            **decorated,
            "alert": alert_payload,
            "delivery": delivery_payload,
        }

    def acknowledge_alert(
        self,
        *,
        acted_by: str = "operator",
        note: str = "",
    ) -> Dict[str, Any]:
        """Mark the current active self-check alert as acknowledged."""

        payload = self.read_status()
        alert_payload = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
        if not bool(alert_payload.get("active")):
            return payload

        now = self.utc_now_iso()
        acknowledged = {
            **self._default_alert_payload(),
            **dict(alert_payload),
            "active": True,
            "state": "acknowledged",
            "acknowledged": True,
            "acknowledged_at": now,
            "acknowledged_by": str(acted_by or "").strip() or "operator",
            "note": str(note or "").strip(),
            "report_generated_at": str(payload.get("generated_at") or "").strip(),
            "report_path": str(self.report_file),
        }
        self._write_json_atomic(self.alert_file, acknowledged)
        return {
            **payload,
            "alert": acknowledged,
            "delivery": self._alert_delivery_payload(
                alert_payload=acknowledged,
                report_payload=payload,
                deliver=False,
            ),
        }

    def _build_payload(self, *, trigger: str) -> Dict[str, Any]:
        warnings: List[Dict[str, str]] = []
        next_actions: List[str] = []

        patch_status = dict(self.build_patch_status() or {})
        patch_run = dict(self.build_patch_run_payload() or {})
        patch_updater = dict(self.build_patch_updater_status() or {})
        patch_health = dict(self.build_patch_health_payload() or {})
        hygiene = dict(self.build_hygiene_status() or {})
        security = dict(self.build_security_status() or {})

        patch_status_message = str(patch_status.get("message") or "").strip()
        patch_status_state = str(patch_status.get("status") or "").strip()
        if patch_status_state == "error":
            warnings.append(
                {
                    "code": "patch_status_error",
                    "severity": "high",
                    "source": "patch_status",
                    "message": patch_status_message or "patch status 확인 중 오류가 발생했습니다.",
                }
            )
            next_actions.append("Git patch status 점검 로그를 확인하고 저장소 상태를 복구합니다.")
        elif patch_status_state == "unavailable":
            warnings.append(
                {
                    "code": "patch_status_unavailable",
                    "severity": "medium",
                    "source": "patch_status",
                    "message": patch_status_message or "patch status를 확인할 수 없습니다.",
                }
            )
            next_actions.append("배포 경로가 Git 저장소인지와 upstream 설정을 확인합니다.")
        if bool(patch_status.get("update_available")):
            warnings.append(
                {
                    "code": "patch_update_available",
                    "severity": "medium",
                    "source": "patch_status",
                    "message": "원격 기준 새 패치가 감지됐습니다.",
                }
            )
            next_actions.append("patch status를 확인하고 patch run 생성 여부를 결정합니다.")
        if bool(patch_status.get("working_tree_dirty")):
            warnings.append(
                {
                    "code": "patch_working_tree_dirty",
                    "severity": "high",
                    "source": "patch_status",
                    "message": "배포 저장소에 로컬 변경 사항이 남아 있습니다.",
                }
            )
            next_actions.append("배포 저장소 working tree를 정리하고 수동 변경 유입 여부를 확인합니다.")
        if int(patch_status.get("ahead_count") or 0) > 0:
            warnings.append(
                {
                    "code": "patch_repository_ahead",
                    "severity": "high",
                    "source": "patch_status",
                    "message": "배포 저장소에 원격보다 앞선 로컬 커밋이 있습니다.",
                }
            )
            next_actions.append("로컬 선행 커밋을 검토하고 upstream과 수동 동기화 여부를 결정합니다.")

        patch_run_status = str(patch_run.get("status") or "").strip()
        if patch_run_status in {"failed", "rollback_failed", "restore_failed"}:
            warnings.append(
                {
                    "code": f"patch_run_{patch_run_status}",
                    "severity": "high",
                    "source": "patch_run",
                    "message": str(patch_run.get("message") or "최근 patch run이 실패 상태입니다."),
                }
            )
            next_actions.append("최근 patch run 실패 사유와 rollback/restore 여부를 확인합니다.")

        patch_updater_status = str(patch_updater.get("status") or "").strip()
        if patch_updater_status in {"offline", "error"}:
            warnings.append(
                {
                    "code": f"patch_updater_{patch_updater_status or 'unknown'}",
                    "severity": "high",
                    "source": "patch_updater",
                    "message": str(patch_updater.get("message") or "updater service 상태가 비정상입니다."),
                }
            )
            next_actions.append("`agenthub-updater.service` 상태와 최근 로그를 확인합니다.")

        failed_checks = [
            str(item).strip()
            for item in (patch_health.get("failed_checks") or [])
            if str(item).strip()
        ]
        if not bool(patch_health.get("ok", True)):
            for check_name in failed_checks or ["unknown"]:
                check_payload = {}
                if isinstance(patch_health.get("checks"), dict):
                    check_payload = dict((patch_health.get("checks") or {}).get(check_name) or {})
                detail = (
                    str(check_payload.get("message") or "").strip()
                    or str(check_payload.get("status") or "").strip()
                    or str(check_payload.get("error") or "").strip()
                    or "post-update health check 실패"
                )
                warnings.append(
                    {
                        "code": f"patch_health_{check_name}",
                        "severity": "high",
                        "source": "patch_health",
                        "message": f"{check_name} check 실패: {detail}",
                    }
                )
            next_actions.append("API/worker/updater/patch lock 상태를 확인하고 failed check를 복구합니다.")

        cleanup_candidate_count = int(
            ((hygiene.get("summary") or {}).get("cleanup_candidate_count") or 0)
        )
        if cleanup_candidate_count > 0:
            warnings.append(
                {
                    "code": "durable_runtime_cleanup_candidates",
                    "severity": "medium",
                    "source": "durable_runtime_hygiene",
                    "message": f"durable runtime cleanup 후보 {cleanup_candidate_count}건이 남아 있습니다.",
                }
            )
            next_actions.append("durable runtime hygiene 카드에서 cleanup 후보를 검토합니다.")
        if bool(((hygiene.get("patch_lock") or {}).get("stale_active_lock"))):
            warnings.append(
                {
                    "code": "durable_runtime_stale_patch_lock",
                    "severity": "high",
                    "source": "durable_runtime_hygiene",
                    "message": "stale patch lock 이 감지됐습니다.",
                }
            )
            next_actions.append("stale patch lock 원인을 확인하고 hygiene cleanup 또는 patch runtime 상태를 점검합니다.")

        for item in security.get("warnings") or []:
            if not isinstance(item, dict):
                continue
            warnings.append(
                {
                    "code": str(item.get("code") or "security_warning"),
                    "severity": str(item.get("severity") or "medium"),
                    "source": "security_governance",
                    "message": str(item.get("message") or "security governance warning"),
                }
            )
        for action in security.get("next_actions") or []:
            if str(action).strip():
                next_actions.append(str(action).strip())

        deduped_warnings = self._dedupe_warnings(warnings)
        deduped_actions = self._dedupe_actions(next_actions)
        overall_status = self._overall_status(deduped_warnings)
        message = (
            "periodic self-check 기준을 충족합니다."
            if not deduped_warnings
            else f"periodic self-check 경고 {len(deduped_warnings)}건이 있습니다."
        )

        return {
            "generated_at": self.utc_now_iso(),
            "trigger": str(trigger or "").strip() or "manual",
            "overall_status": overall_status,
            "message": message,
            "warning_count": len(deduped_warnings),
            "warnings": deduped_warnings,
            "next_actions": deduped_actions,
            "summary": {
                "warning_count": len(deduped_warnings),
                "patch_health_failed_check_count": len(failed_checks),
                "cleanup_candidate_count": cleanup_candidate_count,
                "security_warning_count": int(security.get("warning_count") or 0),
                "update_available": bool(patch_status.get("update_available")),
                "active_patch_run": bool(patch_run.get("active")),
                "active_patch_run_id": str(patch_run.get("patch_run_id") or ""),
                "patch_updater_status": patch_updater_status or "unknown",
            },
            "patch_status": patch_status,
            "patch_run": patch_run,
            "patch_updater": patch_updater,
            "patch_health": patch_health,
            "durable_runtime_hygiene": hygiene,
            "security_governance": security,
            "docs": self._docs_payload(),
        }

    def _decorate_payload(self, payload: Dict[str, Any], *, report_exists: bool) -> Dict[str, Any]:
        generated_at = str(payload.get("generated_at") or "").strip()
        age_minutes = self._age_minutes(generated_at) if generated_at else None
        stale = bool(report_exists and age_minutes is not None and age_minutes > self.stale_after_minutes)
        warnings = self._dedupe_warnings(payload.get("warnings") or [])
        next_actions = self._dedupe_actions(payload.get("next_actions") or [])

        if stale:
            warnings = self._dedupe_warnings(
                [
                    *warnings,
                    {
                        "code": "self_check_report_stale",
                        "severity": "medium",
                        "source": "self_check_report",
                        "message": (
                            f"최근 periodic self-check 보고서가 {age_minutes}분째 갱신되지 않았습니다."
                        ),
                    },
                ]
            )
            next_actions = self._dedupe_actions(
                [
                    *next_actions,
                    "`agenthub-self-check.timer` 와 최근 self-check 실행 로그를 확인합니다.",
                ]
            )

        summary = payload.get("summary")
        if not isinstance(summary, dict):
            summary = self._empty_summary()
        summary = {
            **self._empty_summary(),
            **dict(summary),
            "warning_count": len(warnings),
        }
        if stale and not summary.get("warning_count"):
            summary["warning_count"] = len(warnings)

        overall_status = self._overall_status(warnings, base_status=str(payload.get("overall_status") or "ready"))
        message = str(payload.get("message") or "").strip()
        if stale:
            message = f"최근 periodic self-check 보고서가 {age_minutes}분째 갱신되지 않았습니다."

        decorated = {
            **payload,
            "overall_status": overall_status,
            "message": message,
            "warning_count": len(warnings),
            "warnings": warnings,
            "next_actions": next_actions,
            "summary": summary,
            "docs": self._docs_payload(),
            "report_meta": {
                "path": str(self.report_file),
                "exists": bool(report_exists),
                "age_minutes": age_minutes,
                "stale": stale,
                "stale_after_minutes": self.stale_after_minutes,
            },
        }
        alert_payload = self._current_alert_payload(decorated)
        return {
            **decorated,
            "alert": alert_payload,
            "delivery": self._alert_delivery_payload(
                alert_payload=alert_payload,
                report_payload=decorated,
                deliver=False,
            ),
        }

    def _docs_payload(self) -> Dict[str, str]:
        return {
            "phase_plan": "docs/PHASE7_PATCH_CONTROL_AND_DURABLE_RUNTIME_PLAN.md",
            "execution_plan": "docs/AGENT_PRODUCT_ENGINE_EXECUTION_PLAN.md",
            "service_unit": "systemd/agenthub-self-check.service",
            "timer_unit": "systemd/agenthub-self-check.timer",
            "alert_file": str(self.alert_file),
            "alert_delivery_file": str(self.delivery_file) if self.delivery_file is not None else "",
        }

    def _alert_delivery_payload(
        self,
        *,
        alert_payload: Dict[str, Any],
        report_payload: Dict[str, Any],
        deliver: bool,
    ) -> Dict[str, Any]:
        callback = self.deliver_alert if deliver else self.read_alert_delivery
        if callback is None:
            return {}
        delivery_payload = callback(dict(alert_payload or {}), dict(report_payload or {}))
        return dict(delivery_payload) if isinstance(delivery_payload, dict) else {}

    @staticmethod
    def _empty_summary() -> Dict[str, Any]:
        return {
            "warning_count": 0,
            "patch_health_failed_check_count": 0,
            "cleanup_candidate_count": 0,
            "security_warning_count": 0,
            "update_available": False,
            "active_patch_run": False,
            "active_patch_run_id": "",
            "patch_updater_status": "unknown",
        }

    @staticmethod
    def _dedupe_actions(values: List[Any]) -> List[str]:
        deduped: List[str] = []
        seen: set[str] = set()
        for item in values:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    @staticmethod
    def _dedupe_warnings(values: List[Any]) -> List[Dict[str, str]]:
        deduped: List[Dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            warning = {
                "code": str(item.get("code") or "warning"),
                "severity": str(item.get("severity") or "medium"),
                "source": str(item.get("source") or "runtime"),
                "message": str(item.get("message") or "warning"),
            }
            key = (warning["code"], warning["severity"], warning["message"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(warning)
        return deduped

    @staticmethod
    def _overall_status(
        warnings: List[Dict[str, str]],
        *,
        base_status: str = "ready",
    ) -> str:
        severity_order = {"high": 2, "medium": 1, "low": 0}
        highest = max((severity_order.get(item.get("severity", "medium"), 1) for item in warnings), default=-1)
        if highest >= 2:
            return "critical"
        if warnings:
            return "warning"
        if base_status in {"critical", "warning"}:
            return base_status
        return "ready"

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

    def _current_alert_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = self._derive_alert_payload(payload)
        saved = self._read_json_file(self.alert_file)
        if not isinstance(saved, dict):
            return current
        same_fingerprint = bool(current.get("fingerprint")) and (
            str(saved.get("fingerprint") or "").strip() == str(current.get("fingerprint") or "").strip()
        )
        if current["active"]:
            if not (same_fingerprint and bool(saved.get("active"))):
                return current
            allowed_keys = {
                "state",
                "acknowledged",
                "acknowledged_at",
                "acknowledged_by",
                "note",
                "first_detected_at",
                "last_detected_at",
                "occurrence_count",
            }
        else:
            if bool(saved.get("active")):
                return current
            allowed_keys = {
                "state",
                "acknowledged",
                "acknowledged_at",
                "acknowledged_by",
                "note",
                "first_detected_at",
                "last_detected_at",
                "resolved_at",
                "occurrence_count",
                "fingerprint",
                "warning_codes",
                "warning_count",
                "severity",
                "report_generated_at",
                "report_path",
            }
        return {
            **current,
            **{key: value for key, value in saved.items() if key in allowed_keys},
        }

    def _update_alert_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = self._derive_alert_payload(payload)
        saved = self._read_json_file(self.alert_file)
        saved = dict(saved) if isinstance(saved, dict) else {}
        now = str(payload.get("generated_at") or "").strip() or self.utc_now_iso()

        if current["active"]:
            same_fingerprint = bool(
                saved
                and bool(saved.get("active"))
                and str(saved.get("fingerprint") or "").strip() == current["fingerprint"]
            )
            state = "acknowledged" if same_fingerprint and str(saved.get("state") or "") == "acknowledged" else "open"
            alert_payload = {
                **current,
                "state": state,
                "acknowledged": state == "acknowledged",
                "acknowledged_at": str(saved.get("acknowledged_at") or "").strip() if state == "acknowledged" else "",
                "acknowledged_by": str(saved.get("acknowledged_by") or "").strip() if state == "acknowledged" else "",
                "note": str(saved.get("note") or "").strip() if state == "acknowledged" else "",
                "first_detected_at": (
                    str(saved.get("first_detected_at") or "").strip() if same_fingerprint else now
                )
                or now,
                "last_detected_at": now,
                "resolved_at": "",
                "occurrence_count": int(saved.get("occurrence_count") or 0) + 1 if same_fingerprint else 1,
            }
        else:
            if saved:
                previous_fingerprint = str(saved.get("fingerprint") or "").strip()
                previous_state = str(saved.get("state") or "").strip()
                alert_payload = {
                    **current,
                    "state": "resolved" if previous_fingerprint or previous_state in {"open", "acknowledged"} else "idle",
                    "acknowledged": False,
                    "acknowledged_at": str(saved.get("acknowledged_at") or "").strip(),
                    "acknowledged_by": str(saved.get("acknowledged_by") or "").strip(),
                    "note": str(saved.get("note") or "").strip(),
                    "first_detected_at": str(saved.get("first_detected_at") or "").strip(),
                    "last_detected_at": str(saved.get("last_detected_at") or "").strip(),
                    "resolved_at": now if previous_fingerprint or previous_state in {"open", "acknowledged"} else "",
                    "occurrence_count": int(saved.get("occurrence_count") or 0),
                    "fingerprint": previous_fingerprint,
                }
            else:
                alert_payload = current

        self._write_json_atomic(self.alert_file, alert_payload)
        return alert_payload

    def _derive_alert_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        warnings = [item for item in (payload.get("warnings") or []) if isinstance(item, dict)]
        warning_codes = [str(item.get("code") or "").strip() for item in warnings if str(item.get("code") or "").strip()]
        active = bool(warnings)
        severity = str(payload.get("overall_status") or "").strip() if active else "ready"
        generated_at = str(payload.get("generated_at") or "").strip()
        fingerprint = "|".join(sorted(set(warning_codes))) if warning_codes else ""
        return {
            **self._default_alert_payload(),
            "active": active,
            "state": "open" if active else "idle",
            "warning_codes": warning_codes,
            "warning_count": len(warnings),
            "severity": severity,
            "fingerprint": fingerprint,
            "report_generated_at": generated_at,
            "report_path": str(self.report_file),
        }

    @staticmethod
    def _default_alert_payload() -> Dict[str, Any]:
        return {
            "active": False,
            "state": "idle",
            "acknowledged": False,
            "acknowledged_at": "",
            "acknowledged_by": "",
            "note": "",
            "first_detected_at": "",
            "last_detected_at": "",
            "resolved_at": "",
            "occurrence_count": 0,
            "fingerprint": "",
            "warning_codes": [],
            "warning_count": 0,
            "severity": "ready",
            "report_generated_at": "",
            "report_path": "",
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
