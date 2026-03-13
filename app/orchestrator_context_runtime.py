from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from app.feature_flags import is_feature_enabled
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord


class OrchestratorContextRuntime:
    """Own small orchestrator context helpers and runtime bridges."""

    def __init__(
        self,
        *,
        feature_flags_path: Path | Callable[[], Path],
        memory_runtime_db_path: Path,
        runtime_input_runtime,
        command_templates: Any,
        shell_test_runtime: Any,
    ) -> None:
        self.feature_flags_path = feature_flags_path
        self.memory_runtime_db_path = memory_runtime_db_path
        self.runtime_input_runtime = runtime_input_runtime
        self.command_templates = command_templates
        self.shell_test_runtime = shell_test_runtime
        self._memory_runtime_store: MemoryRuntimeStore | None = None

    def install_command_template_heartbeat(
        self,
        *,
        active_runtime_input_env: Dict[str, str],
        touch_job_heartbeat: Callable[[], None],
    ) -> None:
        """Attach heartbeat hooks and job-scoped env to template/shell runners."""

        try:
            setattr(self.command_templates, "heartbeat_callback", touch_job_heartbeat)
            setattr(self.command_templates, "heartbeat_interval_seconds", 10.0)
            setattr(self.command_templates, "extra_env", active_runtime_input_env)
            setattr(self.shell_test_runtime, "extra_env", active_runtime_input_env)
        except Exception:
            return

    def feature_enabled(self, flag_name: str) -> bool:
        """Read one adaptive feature flag without process restart."""

        path = self.feature_flags_path() if callable(self.feature_flags_path) else self.feature_flags_path
        return is_feature_enabled(path, flag_name)

    def get_memory_runtime_store(self) -> MemoryRuntimeStore:
        """Create the canonical memory DB lazily so normal API boot stays light."""

        if self._memory_runtime_store is None:
            self._memory_runtime_store = MemoryRuntimeStore(self.memory_runtime_db_path)
        return self._memory_runtime_store

    def resolve_runtime_inputs_for_job(self, job: JobRecord) -> Dict[str, object]:
        return self.runtime_input_runtime.resolve_runtime_inputs_for_job(job)

    def set_active_runtime_input_environment(self, job: JobRecord) -> Dict[str, str]:
        """Return one normalized env map for the active job."""

        return self.runtime_input_runtime.build_active_runtime_input_environment(job)

    def write_operator_inputs_artifact(
        self,
        job: JobRecord,
        artifact_path: Path,
    ) -> Dict[str, object]:
        """Persist prompt-safe runtime input context for one job."""

        return self.runtime_input_runtime.write_operator_inputs_artifact(job, artifact_path)
