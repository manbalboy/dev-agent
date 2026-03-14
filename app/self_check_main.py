"""One-shot periodic self-check entrypoint for systemd timer use."""

from __future__ import annotations

from app.config import AppSettings
from app.durable_runtime_hygiene import DurableRuntimeHygieneRuntime
from app.durable_runtime_self_check import DurableRuntimeSelfCheckRuntime
from app.models import utc_now_iso
from app.patch_health_runtime import PatchHealthRuntime
from app.patch_service_runtime import PatchServiceRuntime
from app.patch_updater_runtime import PatchUpdaterRuntime
from app.patch_control_runtime import PatchControlRuntime
from app.dashboard_patch_runtime import DashboardPatchRuntime
from app.security_governance_runtime import SecurityGovernanceRuntime
from app.self_check_alert_delivery_runtime import SelfCheckAlertDeliveryRuntime
from app.store import create_job_store


def run_periodic_self_check() -> dict:
    """Run one persisted periodic self-check report."""

    settings = AppSettings.from_env()
    store = create_job_store(settings)
    patch_service_runtime = PatchServiceRuntime(
        store=store,
        patch_lock_file=settings.patch_lock_file,
        api_service_name=settings.patch_api_service_name,
        worker_service_name=settings.patch_worker_service_name,
        utc_now_iso=utc_now_iso,
    )
    patch_health_runtime = PatchHealthRuntime(
        store=store,
        patch_service_runtime=patch_service_runtime,
        api_health_url=f"http://127.0.0.1:{settings.api_port}/healthz",
        updater_status_file=settings.patch_updater_status_file,
        updater_service_name=settings.patch_updater_service_name,
        utc_now_iso=utc_now_iso,
    )
    delivery_runtime = SelfCheckAlertDeliveryRuntime(
        webhook_url=settings.self_check_alert_webhook_url,
        critical_webhook_url=settings.self_check_alert_critical_webhook_url,
        delivery_file=settings.durable_runtime_self_check_alert_delivery_file,
        utc_now_iso=utc_now_iso,
        repeat_minutes=settings.self_check_alert_repeat_minutes,
        failure_backoff_max_minutes=settings.self_check_alert_failure_backoff_max_minutes,
        timeout_seconds=settings.self_check_alert_webhook_timeout_seconds,
    )
    runtime = DurableRuntimeSelfCheckRuntime(
        build_patch_status=lambda: PatchControlRuntime(
            repo_root=settings.command_config.parent.parent,
            utc_now_iso=utc_now_iso,
        ).build_patch_status(refresh=False),
        build_patch_run_payload=lambda: DashboardPatchRuntime(
            store=store,
            build_patch_control_runtime=lambda: PatchControlRuntime(
                repo_root=settings.command_config.parent.parent,
                utc_now_iso=utc_now_iso,
            ),
            utc_now_iso=utc_now_iso,
        ).get_latest_patch_run_payload(),
        build_patch_updater_status=lambda: PatchUpdaterRuntime(
            store=store,
            status_file=settings.patch_updater_status_file,
            service_name=settings.patch_updater_service_name,
            utc_now_iso=utc_now_iso,
            patch_service_runtime=patch_service_runtime,
        ).read_status_payload(),
        build_patch_health_payload=patch_health_runtime.build_post_update_health_payload,
        build_hygiene_status=lambda: DurableRuntimeHygieneRuntime(
            store=store,
            settings=settings,
            utc_now_iso=utc_now_iso,
            report_file=settings.durable_runtime_hygiene_report_file,
        ).build_hygiene_status(),
        build_security_status=lambda: SecurityGovernanceRuntime(
            settings=settings,
            utc_now_iso=utc_now_iso,
        ).build_status(),
        utc_now_iso=utc_now_iso,
        report_file=settings.durable_runtime_self_check_report_file,
        alert_file=settings.durable_runtime_self_check_alert_file,
        delivery_file=settings.durable_runtime_self_check_alert_delivery_file,
        read_alert_delivery=lambda alert, report: delivery_runtime.read_status(
            alert=alert,
            report=report,
        ),
        deliver_alert=lambda alert, report: delivery_runtime.process_alert(
            alert=alert,
            report=report,
        ),
        stale_after_minutes=settings.self_check_stale_minutes,
    )
    return runtime.run_check(trigger="systemd_timer")


def main() -> None:
    """CLI entrypoint for one self-check execution."""

    payload = run_periodic_self_check()
    summary = payload.get("summary") or {}
    print(
        "[self-check] "
        f"status={payload.get('overall_status', 'unknown')} "
        f"warnings={summary.get('warning_count', 0)} "
        f"failed_checks={summary.get('patch_health_failed_check_count', 0)} "
        f"delivery={((payload.get('delivery') or {}).get('status') or 'disabled')}"
    )


if __name__ == "__main__":
    main()
