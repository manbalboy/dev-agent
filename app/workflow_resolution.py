"""Shared helpers for workflow selection resolution."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from app.workflow_design import default_workflow_template, load_workflows


@dataclass(frozen=True)
class WorkflowSelection:
    """Resolved workflow decision for one job execution."""

    workflow_id: str
    source: str
    warning: str = ""


def load_workflow_catalog(workflows_path: Path) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """Return default workflow id and a workflow lookup map."""

    payload = load_workflows(workflows_path)
    default_workflow_id = str(payload.get("default_workflow_id", "")).strip()
    if not default_workflow_id:
        default_workflow_id = default_workflow_template()["workflow_id"]

    workflows = payload.get("workflows", [])
    by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(workflows, list):
        for item in workflows:
            if not isinstance(item, dict):
                continue
            workflow_id = str(item.get("workflow_id", "")).strip()
            if workflow_id:
                by_id[workflow_id] = item
    return default_workflow_id, by_id


def read_default_workflow_id(workflows_path: Path) -> str:
    """Read default workflow id with safe fallback."""

    default_workflow_id, _ = load_workflow_catalog(workflows_path)
    return default_workflow_id


def list_known_workflow_ids(workflows_path: Path) -> Set[str]:
    """Return all registered workflow ids."""

    _, by_id = load_workflow_catalog(workflows_path)
    return set(by_id)


def read_registered_apps(
    apps_path: Path,
    repository: str,
    default_workflow_id: str = "",
) -> List[Dict[str, str]]:
    """Read app registration list from JSON with one default fallback row."""

    resolved_default_workflow_id = default_workflow_id.strip() or default_workflow_template()["workflow_id"]
    defaults = [
        {
            "code": "default",
            "name": "Default",
            "repository": repository,
            "workflow_id": resolved_default_workflow_id,
            "source_repository": "",
        }
    ]
    if not apps_path.exists():
        return defaults

    try:
        payload = json.loads(apps_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaults

    if not isinstance(payload, list):
        return defaults

    collected: List[Dict[str, str]] = []
    has_default = False
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip().lower()
        if not code:
            continue
        name = str(item.get("name", code)).strip() or code
        app_repository = str(item.get("repository", repository)).strip() or repository
        workflow_id = str(item.get("workflow_id", resolved_default_workflow_id)).strip() or resolved_default_workflow_id
        source_repository = str(item.get("source_repository", "")).strip()
        collected.append(
            {
                "code": code,
                "name": name,
                "repository": app_repository,
                "workflow_id": workflow_id,
                "source_repository": source_repository,
            }
        )
        if code == "default":
            has_default = True

    collected.sort(key=lambda item: item["code"])
    if not has_default:
        collected.insert(0, defaults[0])
    return collected


def write_registered_apps(apps_path: Path, apps: List[Dict[str, str]]) -> None:
    """Persist app list as pretty JSON."""

    dedup: Dict[str, Dict[str, str]] = {}
    for app in apps:
        code = str(app.get("code", "")).strip().lower()
        if not code:
            continue
        name = str(app.get("name", code)).strip() or code
        repository = str(app.get("repository", "")).strip()
        workflow_id = str(app.get("workflow_id", "")).strip() or default_workflow_template()["workflow_id"]
        source_repository = str(app.get("source_repository", "")).strip()
        dedup[code] = {
            "code": code,
            "name": name,
            "repository": repository,
            "workflow_id": workflow_id,
            "source_repository": source_repository,
        }

    if "default" not in dedup:
        dedup["default"] = {
            "code": "default",
            "name": "Default",
            "repository": "",
            "workflow_id": default_workflow_template()["workflow_id"],
            "source_repository": "",
        }

    ordered = [dedup[key] for key in sorted(dedup)]
    apps_path.parent.mkdir(parents=True, exist_ok=True)
    apps_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_workflow_selection(
    *,
    requested_workflow_id: str,
    app_code: str,
    repository: str,
    apps_path: Path,
    workflows_path: Path,
) -> WorkflowSelection:
    """Resolve workflow precedence as job > app > default.

    If a requested or app-mapped workflow id is stale or missing from the current
    workflow catalog, fall back to the default workflow id and return a warning.
    """

    default_workflow_id, known_workflows = load_workflow_catalog(workflows_path)
    known_workflow_ids = set(known_workflows)

    requested = str(requested_workflow_id or "").strip()
    if requested:
        if requested in known_workflow_ids:
            return WorkflowSelection(workflow_id=requested, source="job")
        return WorkflowSelection(
            workflow_id=default_workflow_id,
            source="default",
            warning=f"Requested workflow_id not found: {requested}",
        )

    apps = read_registered_apps(apps_path, repository, default_workflow_id=default_workflow_id)
    normalized_app_code = (app_code or "").strip().lower() or "default"
    matched = next((item for item in apps if item.get("code") == normalized_app_code), None)
    if matched is None:
        matched = next((item for item in apps if item.get("code") == "default"), None)

    app_workflow_id = str((matched or {}).get("workflow_id", "")).strip()
    if app_workflow_id:
        if app_workflow_id in known_workflow_ids:
            return WorkflowSelection(workflow_id=app_workflow_id, source="app")
        return WorkflowSelection(
            workflow_id=default_workflow_id,
            source="default",
            warning=f"App workflow_id not found for app_code={normalized_app_code}: {app_workflow_id}",
        )

    return WorkflowSelection(workflow_id=default_workflow_id, source="default")
