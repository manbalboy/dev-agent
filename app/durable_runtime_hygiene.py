"""Durable runtime hygiene audit and safe cleanup helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Callable, Dict, List

from app.config import AppSettings
from app.models import JobStatus
from app.store import JobStore


class DurableRuntimeHygieneRuntime:
    """Inspect long-running runtime leftovers and prune safe cleanup targets."""

    _ACTIVE_PATCH_STATUSES = {
        "queued",
        "preparing",
        "waiting_updater",
        "draining",
        "updating",
        "restarting",
        "verifying",
        "rollback_requested",
        "rolling_back",
        "rollback_verifying",
        "restore_requested",
        "restoring",
        "restore_verifying",
    }
    _BACKUP_PROTECTED_STATUSES = {
        "failed",
        "rollback_failed",
        "rolled_back",
        "restore_failed",
        "rollback_requested",
        "rolling_back",
        "rollback_verifying",
        "restore_requested",
        "restoring",
        "restore_verifying",
    }
    _BACKUP_PRUNABLE_TERMINAL_STATUSES = {"done", "restored"}

    def __init__(
        self,
        *,
        store: JobStore,
        settings: AppSettings,
        utc_now_iso: Callable[[], str],
        report_file: Path,
    ) -> None:
        self.store = store
        self.settings = settings
        self.utc_now_iso = utc_now_iso
        self.report_file = report_file

    def build_hygiene_status(self) -> Dict[str, Any]:
        """Return one operator-facing audit payload without mutating runtime state."""

        return self._build_payload(apply_cleanup=False)

    def cleanup(self) -> Dict[str, Any]:
        """Remove only safe cleanup candidates and persist one audit report."""

        payload = self._build_payload(apply_cleanup=True)
        self._write_json_atomic(self.report_file, payload)
        payload["last_cleanup"] = self._read_last_cleanup_summary()
        return payload

    def _build_payload(self, *, apply_cleanup: bool) -> Dict[str, Any]:
        now = self._utc_now()
        jobs = self.store.list_jobs()
        jobs_by_id = {job.job_id: job for job in jobs}
        tracked_workspaces = {
            str(self.settings.repository_workspace_path(self._job_execution_repository(job), job.app_code).resolve())
            for job in jobs
        }
        active_workspaces = {
            str(self.settings.repository_workspace_path(self._job_execution_repository(job), job.app_code).resolve())
            for job in jobs
            if str(job.status or "").strip() in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}
        }
        queue_payload = self._analyze_queue(jobs_by_id=jobs_by_id)
        workspace_payload = self._analyze_workspaces(
            tracked_workspaces=tracked_workspaces,
            active_workspaces=active_workspaces,
            now=now,
        )
        patch_backup_payload = self._analyze_patch_backups(now=now)
        patch_lock_payload = self._analyze_patch_lock()
        cleanup = {
            "applied": bool(apply_cleanup),
            "removed_patch_backups": [],
            "removed_invalid_workspaces": [],
            "queue_pruned_entries": [],
            "patch_lock_cleared": False,
            "report_path": str(self.report_file),
        }

        if apply_cleanup:
            cleanup["removed_patch_backups"] = self._cleanup_directories(
                patch_backup_payload["cleanup_candidates"],
            )
            cleanup["removed_invalid_workspaces"] = self._cleanup_directories(
                workspace_payload["invalid_workspace_cleanup_candidates"],
            )
            cleanup["queue_pruned_entries"] = self._apply_queue_cleanup(
                queue_payload["kept_job_ids"],
                queue_payload["cleanup_candidates"],
            )
            if patch_lock_payload["stale_active_lock"]:
                self._clear_patch_lock()
                cleanup["patch_lock_cleared"] = True

        candidate_count = (
            len(queue_payload["cleanup_candidates"])
            + len(workspace_payload["invalid_workspace_cleanup_candidates"])
            + len(patch_backup_payload["cleanup_candidates"])
            + (1 if patch_lock_payload["stale_active_lock"] else 0)
        )
        workspaces_warning_count = (
            len(workspace_payload["invalid_workspace_backups"])
            + len(workspace_payload["unmanaged_workspaces"])
        )
        cleanup_message = self._build_message(
            apply_cleanup=apply_cleanup,
            candidate_count=candidate_count,
            cleanup=cleanup,
            patch_lock_payload=patch_lock_payload,
        )

        return {
            "generated_at": self.utc_now_iso(),
            "retention_days": int(self.settings.durable_retention_days),
            "cleanup_applied": bool(apply_cleanup),
            "message": cleanup_message,
            "summary": {
                "tracked_workspaces": len(tracked_workspaces),
                "active_workspaces": len(active_workspaces),
                "workspace_warning_count": workspaces_warning_count,
                "queue_cleanup_candidate_count": len(queue_payload["cleanup_candidates"]),
                "patch_backup_cleanup_candidate_count": len(patch_backup_payload["cleanup_candidates"]),
                "cleanup_candidate_count": candidate_count,
            },
            "queue": queue_payload,
            "workspaces": workspace_payload,
            "patch_backups": patch_backup_payload,
            "patch_lock": patch_lock_payload,
            "cleanup": cleanup,
            "last_cleanup": self._read_last_cleanup_summary(),
        }

    def _analyze_queue(self, *, jobs_by_id: Dict[str, Any]) -> Dict[str, Any]:
        queue_entries = self._load_queue_entries()
        kept_job_ids: List[str] = []
        seen_job_ids: set[str] = set()
        cleanup_candidates: List[Dict[str, Any]] = []
        duplicate_count = 0
        orphan_count = 0
        stale_status_count = 0

        for position, item in enumerate(queue_entries, start=1):
            job_id = str(item.get("job_id") or "").strip()
            if not job_id:
                cleanup_candidates.append(
                    {"job_id": "", "queue_position": position, "reason": "empty_queue_entry"}
                )
                orphan_count += 1
                continue
            job = jobs_by_id.get(job_id)
            if job is None:
                cleanup_candidates.append(
                    {"job_id": job_id, "queue_position": position, "reason": "missing_job"}
                )
                orphan_count += 1
                continue
            job_status = str(job.status or "").strip()
            if job_status != JobStatus.QUEUED.value:
                cleanup_candidates.append(
                    {
                        "job_id": job_id,
                        "queue_position": position,
                        "reason": f"job_status_{job_status or 'unknown'}",
                    }
                )
                stale_status_count += 1
                continue
            if job_id in seen_job_ids:
                cleanup_candidates.append(
                    {"job_id": job_id, "queue_position": position, "reason": "duplicate_queue_entry"}
                )
                duplicate_count += 1
                continue
            kept_job_ids.append(job_id)
            seen_job_ids.add(job_id)

        return {
            "total_entries": len(queue_entries),
            "kept_entries": len(kept_job_ids),
            "duplicate_entry_count": duplicate_count,
            "orphan_entry_count": orphan_count,
            "stale_status_entry_count": stale_status_count,
            "cleanup_candidates": cleanup_candidates,
            "kept_job_ids": kept_job_ids,
        }

    def _analyze_workspaces(
        self,
        *,
        tracked_workspaces: set[str],
        active_workspaces: set[str],
        now: datetime,
    ) -> Dict[str, Any]:
        invalid_backups: List[Dict[str, Any]] = []
        invalid_cleanup_candidates: List[Dict[str, Any]] = []
        unmanaged_workspaces: List[Dict[str, Any]] = []

        for repository_path in self._iter_workspace_directories():
            resolved = str(repository_path.resolve())
            item = {
                "path": str(repository_path),
                "app_code": repository_path.parent.name,
                "name": repository_path.name,
                "age_days": self._age_days(repository_path, now),
                "tracked": resolved in tracked_workspaces,
                "active": resolved in active_workspaces,
            }
            if "__invalid_" in repository_path.name:
                invalid_backups.append(item)
                if item["age_days"] >= int(self.settings.durable_retention_days):
                    invalid_cleanup_candidates.append(item)
                continue
            if resolved not in tracked_workspaces:
                unmanaged_workspaces.append(item)

        return {
            "invalid_workspace_backups": invalid_backups,
            "invalid_workspace_cleanup_candidates": invalid_cleanup_candidates,
            "unmanaged_workspaces": unmanaged_workspaces,
        }

    def _analyze_patch_backups(self, *, now: datetime) -> Dict[str, Any]:
        references = self._build_patch_backup_references()
        backups: List[Dict[str, Any]] = []
        cleanup_candidates: List[Dict[str, Any]] = []

        if not self.settings.patch_backups_dir.exists():
            return {
                "backups": backups,
                "cleanup_candidates": cleanup_candidates,
            }

        for backup_dir in sorted(self.settings.patch_backups_dir.iterdir()):
            if not backup_dir.is_dir():
                continue
            manifest_path = backup_dir / "manifest.json"
            manifest = self._read_json(manifest_path)
            manifest_backup_id = ""
            if isinstance(manifest, dict):
                manifest_backup_id = str(manifest.get("backup_id") or "").strip()
            backup_id = manifest_backup_id or backup_dir.name
            referenced_runs = references.get(backup_id, [])
            statuses = [str(item.get("status") or "") for item in referenced_runs]
            age_days = self._age_days(backup_dir, now)
            protected = any(status in self._BACKUP_PROTECTED_STATUSES for status in statuses)
            cleanup_eligible = bool(age_days >= int(self.settings.durable_retention_days)) and (
                not referenced_runs
                or (
                    not protected
                    and all(status in self._BACKUP_PRUNABLE_TERMINAL_STATUSES for status in statuses)
                )
            )
            item = {
                "backup_id": backup_id,
                "path": str(backup_dir),
                "manifest_path": str(manifest_path),
                "manifest_exists": manifest_path.exists(),
                "age_days": age_days,
                "referenced_patch_runs": referenced_runs,
                "cleanup_eligible": cleanup_eligible,
                "cleanup_reason": self._describe_patch_backup_reason(
                    referenced_runs=referenced_runs,
                    protected=protected,
                    cleanup_eligible=cleanup_eligible,
                ),
            }
            backups.append(item)
            if cleanup_eligible:
                cleanup_candidates.append(item)

        return {
            "backups": backups,
            "cleanup_candidates": cleanup_candidates,
        }

    def _analyze_patch_lock(self) -> Dict[str, Any]:
        payload = self._read_json(self.settings.patch_lock_file)
        if not isinstance(payload, dict):
            return {
                "active": False,
                "patch_run_id": "",
                "status": "idle",
                "stale_active_lock": False,
            }
        active_patch_run_ids = {
            patch_run.patch_run_id
            for patch_run in self.store.list_patch_runs()
            if str(patch_run.status or "").strip() in self._ACTIVE_PATCH_STATUSES
        }
        patch_run_id = str(payload.get("patch_run_id") or "").strip()
        active = bool(payload.get("active"))
        stale_active_lock = active and (not patch_run_id or patch_run_id not in active_patch_run_ids)
        return {
            "active": active,
            "patch_run_id": patch_run_id,
            "status": str(payload.get("status") or ""),
            "updated_at": str(payload.get("updated_at") or ""),
            "stale_active_lock": stale_active_lock,
            "details": dict(payload.get("details") or {}),
        }

    def _build_patch_backup_references(self) -> Dict[str, List[Dict[str, str]]]:
        references: Dict[str, List[Dict[str, str]]] = {}
        for patch_run in self.store.list_patch_runs():
            details = dict(patch_run.details or {})
            backup_manifest = details.get("backup_manifest")
            if not isinstance(backup_manifest, dict):
                continue
            backup_id = str(backup_manifest.get("backup_id") or "").strip()
            if not backup_id:
                continue
            references.setdefault(backup_id, []).append(
                {
                    "patch_run_id": patch_run.patch_run_id,
                    "status": str(patch_run.status or "").strip(),
                }
            )
        return references

    def _iter_workspace_directories(self) -> List[Path]:
        items: List[Path] = []
        if not self.settings.workspace_dir.exists():
            return items
        for app_dir in sorted(self.settings.workspace_dir.iterdir()):
            if not app_dir.is_dir() or app_dir.name.startswith("."):
                continue
            for repository_path in sorted(app_dir.iterdir()):
                if repository_path.is_dir():
                    items.append(repository_path)
        return items

    def _load_queue_entries(self) -> List[Dict[str, Any]]:
        if self.settings.store_backend == "sqlite":
            if not self.settings.sqlite_file.exists():
                return []
            with sqlite3.connect(self.settings.sqlite_file) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT id, job_id FROM queue ORDER BY id ASC").fetchall()
            return [{"id": int(row["id"]), "job_id": str(row["job_id"])} for row in rows]

        payload = self._read_json(self.settings.queue_file)
        if not isinstance(payload, list):
            return []
        return [{"id": index + 1, "job_id": str(item or "")} for index, item in enumerate(payload)]

    def _apply_queue_cleanup(
        self,
        kept_job_ids: List[str],
        cleanup_candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if self.settings.store_backend == "sqlite":
            self._rewrite_sqlite_queue(kept_job_ids)
        else:
            self._write_json_atomic(self.settings.queue_file, kept_job_ids)
        return cleanup_candidates

    def _rewrite_sqlite_queue(self, job_ids: List[str]) -> None:
        if not self.settings.sqlite_file.exists():
            return
        with sqlite3.connect(self.settings.sqlite_file) as conn:
            conn.execute("DELETE FROM queue")
            conn.executemany("INSERT INTO queue (job_id) VALUES (?)", [(job_id,) for job_id in job_ids])
            conn.commit()

    def _cleanup_directories(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        removed: List[Dict[str, Any]] = []
        for item in items:
            path = Path(str(item.get("path") or "")).resolve()
            if not path.exists():
                continue
            shutil.rmtree(path)
            removed.append(dict(item))
        return removed

    def _clear_patch_lock(self) -> None:
        payload = {
            "active": False,
            "patch_run_id": "",
            "status": "cleared_by_hygiene",
            "message": "활성 patch run이 없어 stale patch lock을 정리했습니다.",
            "updated_at": self.utc_now_iso(),
            "details": {"cleanup_source": "durable_runtime_hygiene"},
        }
        self._write_json_atomic(self.settings.patch_lock_file, payload)

    def _build_message(
        self,
        *,
        apply_cleanup: bool,
        candidate_count: int,
        cleanup: Dict[str, Any],
        patch_lock_payload: Dict[str, Any],
    ) -> str:
        if not apply_cleanup:
            if candidate_count <= 0:
                return "정리할 durable runtime 후보가 없습니다."
            return f"정리 후보 {candidate_count}건을 찾았습니다. 안전한 대상만 정리 실행할 수 있습니다."

        removed_count = (
            len(cleanup["removed_patch_backups"])
            + len(cleanup["removed_invalid_workspaces"])
            + len(cleanup["queue_pruned_entries"])
            + (1 if cleanup["patch_lock_cleared"] else 0)
        )
        if removed_count <= 0:
            if patch_lock_payload["stale_active_lock"]:
                return "정리 실행을 시도했지만 stale patch lock만 확인됐고 실제 변경은 없었습니다."
            return "정리 실행 대상이 없었습니다."
        return f"durable runtime hygiene 정리를 적용했습니다. 총 {removed_count}건을 정리했습니다."

    def _read_last_cleanup_summary(self) -> Dict[str, Any]:
        payload = self._read_json(self.report_file)
        if not isinstance(payload, dict):
            return {}
        cleanup = payload.get("cleanup")
        summary = payload.get("summary")
        return {
            "generated_at": str(payload.get("generated_at") or ""),
            "message": str(payload.get("message") or ""),
            "cleanup_applied": bool(payload.get("cleanup_applied")),
            "cleanup": dict(cleanup or {}) if isinstance(cleanup, dict) else {},
            "summary": dict(summary or {}) if isinstance(summary, dict) else {},
        }

    def _describe_patch_backup_reason(
        self,
        *,
        referenced_runs: List[Dict[str, str]],
        protected: bool,
        cleanup_eligible: bool,
    ) -> str:
        if cleanup_eligible and not referenced_runs:
            return "orphan_patch_backup"
        if cleanup_eligible:
            return "retention_expired_terminal_patch_backup"
        if protected:
            return "restore_candidate_kept"
        if referenced_runs:
            return "referenced_patch_backup"
        return "retained_by_policy"

    @staticmethod
    def _job_execution_repository(job: Any) -> str:
        source_repository = str(getattr(job, "source_repository", "") or "").strip()
        return source_repository or str(getattr(job, "repository", "") or "").strip()

    def _utc_now(self) -> datetime:
        return self._parse_iso(self.utc_now_iso())

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip())
        except ValueError:
            parsed = datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _age_days(cls, path: Path, now: datetime) -> int:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        delta_seconds = max(0.0, (now - modified).total_seconds())
        return int(delta_seconds // 86400)

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
