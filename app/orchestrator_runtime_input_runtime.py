"""Job-scoped runtime input resolution/runtime bridge for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict

from app.models import JobRecord


class OrchestratorRuntimeInputRuntime:
    """Resolve runtime inputs, build env bridges, and write prompt-safe artifacts."""

    def __init__(
        self,
        *,
        store,
        resolve_runtime_inputs,
        normalize_env_var_name: Callable[[str], str],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.store = store
        self.resolve_runtime_inputs = resolve_runtime_inputs
        self.normalize_env_var_name = normalize_env_var_name
        self.utc_now_iso = utc_now_iso

    def resolve_runtime_inputs_for_job(self, job: JobRecord) -> Dict[str, object]:
        """Resolve operator-provided runtime inputs for one job."""

        resolved = self.resolve_runtime_inputs(
            self.store.list_runtime_inputs(),
            repository=job.repository,
            app_code=job.app_code,
            job_id=job.job_id,
            integration_registry_entries=self.store.list_integration_registry_entries(),
        )
        if isinstance(resolved, dict):
            return resolved
        return {
            "resolved": [],
            "pending": [],
            "blocked": [],
            "environment": {},
            "blocked_environment": {},
        }

    def build_active_runtime_input_environment(self, job: JobRecord) -> Dict[str, str]:
        """Return one normalized shell/template env map for one job."""

        resolved = self.resolve_runtime_inputs_for_job(job)
        environment = resolved.get("environment", {}) if isinstance(resolved, dict) else {}
        return {
            self.normalize_env_var_name(key): str(value)
            for key, value in dict(environment or {}).items()
            if str(key).strip() and str(value).strip()
        }

    def write_operator_inputs_artifact(
        self,
        job: JobRecord,
        artifact_path: Path,
    ) -> Dict[str, object]:
        """Persist prompt-safe runtime input context for one job."""

        resolved = self.resolve_runtime_inputs_for_job(job)
        payload = {
            "generated_at": self.utc_now_iso(),
            "job_id": job.job_id,
            "repository": job.repository,
            "app_code": job.app_code,
            "resolved_inputs": resolved.get("resolved", []) if isinstance(resolved, dict) else [],
            "pending_inputs": resolved.get("pending", []) if isinstance(resolved, dict) else [],
            "blocked_inputs": resolved.get("blocked", []) if isinstance(resolved, dict) else [],
            "available_env_vars": sorted(dict(resolved.get("environment", {}) or {}).keys()) if isinstance(resolved, dict) else [],
            "blocked_env_vars": sorted(dict(resolved.get("blocked_environment", {}) or {}).keys()) if isinstance(resolved, dict) else [],
        }
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload
