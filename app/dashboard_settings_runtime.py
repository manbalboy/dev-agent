"""Settings helper runtime for workflow, feature-flag, and agent-config APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

from fastapi import HTTPException


class DashboardSettingsRuntime:
    """Encapsulate dashboard settings CRUD and read helpers."""

    def __init__(
        self,
        *,
        workflows_config_path: Path,
        feature_flags_config_path: Path,
        command_config_path: Path,
        env_path: Path,
        enable_escalation_fallback: bool,
        schema_payload: Callable[[], Dict[str, Any]],
        load_workflows: Callable[[Path], Dict[str, Any]],
        save_workflows: Callable[[Path, Dict[str, Any]], None],
        validate_workflow: Callable[[Dict[str, Any]], tuple[bool, List[str]]],
        default_workflow_template: Callable[[], Dict[str, Any]],
        feature_flags_payload: Callable[[Path], Dict[str, Any]],
        write_feature_flags: Callable[[Path, Dict[str, Any]], Dict[str, bool]],
        load_agent_template_config: Callable[..., Dict[str, Any]],
        update_agent_template_config: Callable[..., Dict[str, Any]],
        collect_agent_cli_status: Callable[[Path], Dict[str, Any]],
        collect_agent_model_status: Callable[[Path], Dict[str, Any]],
    ) -> None:
        self.workflows_config_path = workflows_config_path
        self.feature_flags_config_path = feature_flags_config_path
        self.command_config_path = command_config_path
        self.env_path = env_path
        self.enable_escalation_fallback = enable_escalation_fallback
        self._schema_payload = schema_payload
        self._load_workflows = load_workflows
        self._save_workflows = save_workflows
        self._validate_workflow = validate_workflow
        self._default_workflow_template = default_workflow_template
        self._feature_flags_payload = feature_flags_payload
        self._write_feature_flags = write_feature_flags
        self._load_agent_template_config = load_agent_template_config
        self._update_agent_template_config = update_agent_template_config
        self._collect_agent_cli_status = collect_agent_cli_status
        self._collect_agent_model_status = collect_agent_model_status

    def workflow_schema(self) -> Dict[str, Any]:
        """Return workflow editor schema metadata."""

        return self._schema_payload()

    def list_workflows(self) -> Dict[str, Any]:
        """Return saved workflows and current default workflow id."""

        return self._load_workflows(self.workflows_config_path)

    def validate_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Validate one workflow definition without saving."""

        ok, errors = self._validate_workflow(workflow)
        return {"ok": ok, "errors": errors}

    def save_workflow(self, workflow: Dict[str, Any], *, set_default: bool) -> Dict[str, Any]:
        """Create or update one workflow definition."""

        ok, errors = self._validate_workflow(workflow)
        if not ok:
            raise HTTPException(status_code=400, detail={"message": "workflow validation failed", "errors": errors})

        saved = self._load_workflows(self.workflows_config_path)
        workflows = saved.get("workflows", [])
        if not isinstance(workflows, list):
            workflows = []

        workflow_id = str(workflow.get("workflow_id", "")).strip()
        replaced = False
        next_workflows: List[Dict[str, Any]] = []
        for item in workflows:
            if isinstance(item, dict) and str(item.get("workflow_id", "")).strip() == workflow_id:
                next_workflows.append(workflow)
                replaced = True
                continue
            if isinstance(item, dict):
                next_workflows.append(item)
        if not replaced:
            next_workflows.append(workflow)

        saved["workflows"] = next_workflows
        if set_default or not str(saved.get("default_workflow_id", "")).strip():
            saved["default_workflow_id"] = workflow_id
        if saved.get("default_workflow_id") == "":
            saved["default_workflow_id"] = self._default_workflow_template()["workflow_id"]

        self._save_workflows(self.workflows_config_path, saved)
        return {
            "saved": True,
            "workflow_id": workflow_id,
            "default_workflow_id": saved.get("default_workflow_id"),
        }

    def set_default_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Set one registered workflow as default."""

        saved = self._load_workflows(self.workflows_config_path)
        workflows = saved.get("workflows", [])
        normalized_workflow_id = str(workflow_id or "").strip()
        known_workflow_ids = {
            str(item.get("workflow_id", "")).strip()
            for item in workflows
            if isinstance(item, dict)
        }
        if normalized_workflow_id not in known_workflow_ids:
            raise HTTPException(status_code=400, detail=f"등록되지 않은 workflow_id 입니다: {normalized_workflow_id}")

        saved["default_workflow_id"] = normalized_workflow_id
        self._save_workflows(self.workflows_config_path, saved)
        return {"saved": True, "default_workflow_id": normalized_workflow_id}

    def get_feature_flags(self) -> Dict[str, Any]:
        """Return adaptive feature flags for settings/admin UI."""

        return self._feature_flags_payload(self.feature_flags_config_path)

    def save_feature_flags(self, flags: Dict[str, Any]) -> Dict[str, Any]:
        """Persist adaptive feature flags."""

        normalized = self._write_feature_flags(self.feature_flags_config_path, flags)
        return {"saved": True, **self._feature_flags_payload(self.feature_flags_config_path), "flags": normalized}

    def get_agent_config(self) -> Dict[str, Any]:
        """Return editable command templates for dashboard form."""

        return self._load_agent_template_config(
            self.command_config_path,
            self.env_path,
            enable_escalation_fallback=self.enable_escalation_fallback,
        )

    def update_agent_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update planner/coder/reviewer templates in command config file."""

        return self._update_agent_template_config(
            self.command_config_path,
            self.env_path,
            planner=str(payload.get("planner", "")),
            coder=str(payload.get("coder", "")),
            reviewer=str(payload.get("reviewer", "")),
            copilot=str(payload.get("copilot", "")),
            escalation=str(payload.get("escalation", "")),
            enable_escalation=bool(payload.get("enable_escalation", False)),
        )

    def get_agent_cli_status(self) -> Dict[str, Any]:
        """Check whether Gemini/Codex CLIs are executable."""

        return self._collect_agent_cli_status(self.command_config_path)

    def get_agent_model_status(self) -> Dict[str, Any]:
        """Return inferred model settings for Gemini/Codex."""

        return self._collect_agent_model_status(self.command_config_path)
