from __future__ import annotations

from pathlib import Path

from app.config import AppSettings
from app.security_governance_runtime import SecurityGovernanceRuntime


def _make_settings(**overrides) -> AppSettings:
    base = dict(
        webhook_secret="0123456789abcdef0123456789abcdef",
        allowed_repository="owner/repo",
        data_dir=Path("/tmp/data"),
        workspace_dir=Path("/tmp/workspaces"),
        max_retries=3,
        test_command="echo test",
        test_command_secondary="echo test",
        test_command_implement="echo test",
        test_command_fix="echo test",
        test_command_secondary_implement="echo test",
        test_command_secondary_fix="echo test",
        tester_primary_name="gpt",
        tester_secondary_name="gemini",
        command_config=Path("/tmp/ai_commands.json"),
        worker_poll_seconds=1,
        worker_stale_running_seconds=600,
        worker_max_auto_recoveries=2,
        default_branch="main",
        enable_escalation=False,
        enable_stage_md_commits=True,
        api_port=8321,
        store_backend="json",
        sqlite_file=Path("/tmp/agenthub.db"),
        public_base_url="https://agenthub.example.com",
        enforce_https=True,
        trust_x_forwarded_proto=True,
        cors_allow_all=False,
        cors_origins="https://agenthub.example.com",
        docker_preview_enabled=False,
    )
    base.update(overrides)
    return AppSettings(**base)


def test_security_governance_runtime_reports_ready_for_hardened_settings() -> None:
    runtime = SecurityGovernanceRuntime(
        settings=_make_settings(),
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
    )

    payload = runtime.build_status()

    assert payload["overall_status"] == "ready"
    assert payload["warning_count"] == 0
    assert payload["transport"]["https_url"] is True
    assert payload["transport"]["https_enforced"] is True
    assert payload["webhook_secret"]["status"] == "strong"
    assert payload["operator_checklist"]["production_ready"] is True
    assert payload["recommended_env"]["AGENTHUB_ENFORCE_HTTPS"] == "true"
    assert payload["docs"]["tls_runbook"] == "docs/REVERSE_PROXY_TLS_RUNBOOK.md"


def test_security_governance_runtime_reports_critical_for_insecure_settings() -> None:
    runtime = SecurityGovernanceRuntime(
        settings=_make_settings(
            webhook_secret="test-secret",
            public_base_url="http://localhost:8321",
            enforce_https=False,
            trust_x_forwarded_proto=False,
            cors_allow_all=True,
            cors_origins="*",
        ),
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
    )

    payload = runtime.build_status()

    assert payload["overall_status"] == "critical"
    codes = {item["code"] for item in payload["warnings"]}
    assert "public_base_url_not_https" in codes
    assert "https_not_enforced" in codes
    assert "cors_too_permissive" in codes
    assert "webhook_secret_test_like" in codes
    checklist = {item["code"]: item["ok"] for item in payload["operator_checklist"]["items"]}
    assert checklist["public_base_url_https"] is False
    assert checklist["https_enforced"] is False
    assert checklist["forwarded_proto_trusted"] is False
    assert checklist["cors_restricted"] is False
    assert checklist["webhook_secret_strong"] is False
