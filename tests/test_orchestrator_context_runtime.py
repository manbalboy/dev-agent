from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator_context_runtime import OrchestratorContextRuntime
from app.orchestrator_runtime_input_runtime import OrchestratorRuntimeInputRuntime


class _Store:
    def __init__(self) -> None:
        self.runtime_inputs = [
            {
                "request_id": "req-1",
                "repository": "manbalboy/dev-agent",
                "app_code": "demo",
                "job_id": "job-1",
                "env_var_name": "demo_key",
                "value": "secret",
                "status": "provided",
            }
        ]

    def list_runtime_inputs(self):
        return list(self.runtime_inputs)

    def list_integration_registry_entries(self):
        return []


class _CommandTemplates:
    pass


class _ShellRuntime:
    pass


class _Job:
    repository = "manbalboy/dev-agent"
    app_code = "demo"
    job_id = "job-1"


def _build_runtime(tmp_path: Path) -> OrchestratorContextRuntime:
    store = _Store()
    runtime_input_runtime = OrchestratorRuntimeInputRuntime(
        store=store,
        resolve_runtime_inputs=lambda runtime_inputs, **kwargs: {
            "resolved": [{"env_var_name": "DEMO_KEY"}],
            "pending": [],
            "blocked": [],
            "environment": {"demo_key": "secret"},
            "blocked_environment": {},
        },
        normalize_env_var_name=lambda value: str(value).upper(),
        utc_now_iso=lambda: "2026-03-13T00:00:00Z",
    )
    return OrchestratorContextRuntime(
        feature_flags_path=tmp_path / "feature_flags.json",
        memory_runtime_db_path=tmp_path / "memory_runtime.db",
        runtime_input_runtime=runtime_input_runtime,
        command_templates=_CommandTemplates(),
        shell_test_runtime=_ShellRuntime(),
    )


def test_orchestrator_context_runtime_installs_heartbeat_and_env_bridge(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)

    runtime.install_command_template_heartbeat(
        active_runtime_input_env={"DEMO_KEY": "secret"},
        touch_job_heartbeat=lambda: None,
    )

    assert runtime.command_templates.extra_env == {"DEMO_KEY": "secret"}
    assert runtime.shell_test_runtime.extra_env == {"DEMO_KEY": "secret"}
    assert runtime.command_templates.heartbeat_interval_seconds == 10.0
    assert callable(runtime.command_templates.heartbeat_callback)


def test_orchestrator_context_runtime_reads_feature_flag_and_lazy_store(tmp_path: Path) -> None:
    flags_path = tmp_path / "feature_flags.json"
    flags_path.write_text(json.dumps({"flags": {"assistant_diagnosis_loop": True}}), encoding="utf-8")
    runtime = _build_runtime(tmp_path)
    runtime.feature_flags_path = flags_path

    assert runtime.feature_enabled("assistant_diagnosis_loop") is True
    store = runtime.get_memory_runtime_store()
    assert store is runtime.get_memory_runtime_store()


def test_orchestrator_context_runtime_resolves_inputs_and_writes_artifact(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    job = _Job()

    resolved = runtime.resolve_runtime_inputs_for_job(job)
    assert resolved["environment"] == {"demo_key": "secret"}

    active_env = runtime.set_active_runtime_input_environment(job)
    assert active_env == {"DEMO_KEY": "secret"}

    artifact_path = tmp_path / "OPERATOR_INPUTS.json"
    payload = runtime.write_operator_inputs_artifact(job, artifact_path)
    assert payload["job_id"] == "job-1"
    assert payload["available_env_vars"] == ["demo_key"]
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved["job_id"] == "job-1"
