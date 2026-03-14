"""App registry helpers extracted from dashboard write actions."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence


class DashboardAppRegistryRuntime:
    """Encapsulate app registration and app-to-workflow mapping helpers."""

    def __init__(
        self,
        *,
        allowed_repository: str,
        track_choices: Sequence[str],
        read_registered_apps: Callable[..., List[Dict[str, str]]],
        write_registered_apps: Callable[[Any, List[Dict[str, str]]], None],
        read_default_workflow_id: Callable[[Any], str],
        load_workflows: Callable[[Any], Dict[str, Any]],
        default_workflow_template: Callable[[], Dict[str, Any]],
        normalize_app_code: Callable[[str], str],
        normalize_repository_ref: Callable[[str], str],
        ensure_label: Callable[[str, str, str, str], None],
        apps_config_path: Any,
        workflows_config_path: Any,
    ) -> None:
        self.allowed_repository = allowed_repository
        self.track_choices = sorted(str(item).strip() for item in track_choices if str(item).strip())
        self.read_registered_apps = read_registered_apps
        self.write_registered_apps = write_registered_apps
        self.read_default_workflow_id = read_default_workflow_id
        self.load_workflows = load_workflows
        self.default_workflow_template = default_workflow_template
        self.normalize_app_code = normalize_app_code
        self.normalize_repository_ref = normalize_repository_ref
        self.ensure_label = ensure_label
        self.apps_config_path = apps_config_path
        self.workflows_config_path = workflows_config_path

    def list_apps(self) -> Dict[str, Any]:
        """Return app list payload for dashboard dropdowns."""

        default_workflow_id = self.read_default_workflow_id(self.workflows_config_path)
        return {
            "apps": self.read_registered_apps(
                self.apps_config_path,
                self.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
            "tracks": list(self.track_choices),
            "default_workflow_id": default_workflow_id,
        }

    def upsert_app(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update one app registration entry."""

        code = self.normalize_app_code(str(payload.get("code", "")))
        if not code:
            raise ValueError("앱 코드는 영문/숫자/-/_ 형식이어야 합니다.")
        if code == "default":
            raise ValueError("default 코드는 예약되어 있습니다.")

        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("앱 표시명을 입력해주세요.")

        raw_source_repository = str(payload.get("source_repository", "") or "")
        source_repository = self.normalize_repository_ref(raw_source_repository)
        if raw_source_repository.strip() and not source_repository:
            raise ValueError(
                "source_repository는 GitHub owner/repo 또는 https://github.com/owner/repo(.git) 형식이어야 합니다."
            )

        default_workflow_id, known_workflow_ids = self._load_known_workflow_ids()
        requested_workflow_id = str(payload.get("workflow_id", "") or "").strip()
        workflow_id = requested_workflow_id or default_workflow_id
        if workflow_id and workflow_id not in known_workflow_ids:
            raise ValueError(f"등록되지 않은 workflow_id 입니다: {workflow_id}")

        apps = self.read_registered_apps(
            self.apps_config_path,
            self.allowed_repository,
            default_workflow_id=default_workflow_id,
        )
        replaced = False
        updated: List[Dict[str, str]] = []
        next_item = {
            "code": code,
            "name": name,
            "repository": self.allowed_repository,
            "workflow_id": workflow_id,
            "source_repository": source_repository,
        }
        for app in apps:
            if app["code"] == "default":
                updated.append(app)
                continue
            if app["code"] == code:
                updated.append(next_item)
                replaced = True
                continue
            updated.append(app)
        if not replaced:
            updated.append(next_item)

        self.write_registered_apps(self.apps_config_path, updated)
        self.ensure_label(
            self.allowed_repository,
            f"app:{code}",
            "0052CC",
            f"AgentHub app namespace ({code})",
        )
        for track in self.track_choices:
            self.ensure_label(
                self.allowed_repository,
                f"track:{track}",
                "5319E7",
                f"AgentHub work type ({track})",
            )
        return {
            "saved": True,
            "apps": self.read_registered_apps(
                self.apps_config_path,
                self.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }

    def delete_app(self, app_code: str) -> Dict[str, Any]:
        """Delete one app registration entry."""

        code = self.normalize_app_code(app_code)
        if not code or code == "default":
            raise ValueError("삭제할 수 없는 앱 코드입니다.")

        default_workflow_id = self.read_default_workflow_id(self.workflows_config_path)
        apps = self.read_registered_apps(
            self.apps_config_path,
            self.allowed_repository,
            default_workflow_id=default_workflow_id,
        )
        filtered = [app for app in apps if app["code"] != code]
        self.write_registered_apps(self.apps_config_path, filtered)
        return {
            "deleted": True,
            "apps": self.read_registered_apps(
                self.apps_config_path,
                self.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }

    def map_app_workflow(self, app_code: str, workflow_id: str) -> Dict[str, Any]:
        """Bind one existing app to one registered workflow id."""

        code = self.normalize_app_code(app_code)
        if not code:
            raise ValueError("유효하지 않은 앱 코드입니다.")

        normalized_workflow_id = str(workflow_id or "").strip()
        default_workflow_id, known_workflow_ids = self._load_known_workflow_ids()
        if normalized_workflow_id not in known_workflow_ids:
            raise ValueError(f"등록되지 않은 workflow_id 입니다: {normalized_workflow_id}")

        apps = self.read_registered_apps(
            self.apps_config_path,
            self.allowed_repository,
            default_workflow_id=default_workflow_id,
        )
        found = False
        updated: List[Dict[str, str]] = []
        for app in apps:
            if app["code"] == code:
                copied = dict(app)
                copied["workflow_id"] = normalized_workflow_id
                updated.append(copied)
                found = True
                continue
            updated.append(app)

        if not found:
            raise KeyError(f"앱을 찾을 수 없습니다: {code}")

        self.write_registered_apps(self.apps_config_path, updated)
        return {
            "saved": True,
            "app_code": code,
            "workflow_id": normalized_workflow_id,
            "apps": self.read_registered_apps(
                self.apps_config_path,
                self.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }

    def _load_known_workflow_ids(self) -> tuple[str, set[str]]:
        workflows_payload = self.load_workflows(self.workflows_config_path)
        default_workflow_id = (
            str(workflows_payload.get("default_workflow_id", "")).strip()
            or self.default_workflow_template()["workflow_id"]
        )
        workflows = workflows_payload.get("workflows", [])
        known_workflow_ids = {
            str(item.get("workflow_id", "")).strip()
            for item in workflows
            if isinstance(item, dict)
        }
        return default_workflow_id, known_workflow_ids
