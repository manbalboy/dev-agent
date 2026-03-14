"""Patch backup helpers for updater-driven maintenance flows."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Mapping


class PatchBackupRuntime:
    """Create one operator-readable backup snapshot before patch actions."""

    def __init__(
        self,
        *,
        backups_dir: Path,
        data_root: Path,
        state_files: Mapping[str, Path],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.backups_dir = backups_dir
        self.data_root = data_root
        self.state_files = {str(name): Path(path) for name, path in state_files.items()}
        self.utc_now_iso = utc_now_iso

    def create_backup(
        self,
        *,
        patch_run_id: str,
        repo_root: str | Path,
        branch: str,
        source_commit: str,
        target_commit: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Copy core runtime state files into one backup directory and return manifest."""

        created_at = self.utc_now_iso()
        backup_id = self._build_backup_id(patch_run_id, created_at)
        backup_dir = self.backups_dir / backup_id
        backup_dir.mkdir(parents=True, exist_ok=True)

        files: list[Dict[str, Any]] = []
        total_size_bytes = 0
        for logical_name, source_path in self.state_files.items():
            if not source_path.exists() or not source_path.is_file():
                continue
            relative_path = self._relative_backup_path(source_path)
            destination_path = backup_dir / relative_path
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
            size_bytes = int(destination_path.stat().st_size)
            total_size_bytes += size_bytes
            files.append(
                {
                    "logical_name": logical_name,
                    "source_path": str(source_path),
                    "relative_path": relative_path.as_posix(),
                    "destination_path": str(destination_path),
                    "size_bytes": size_bytes,
                }
            )

        manifest = {
            "backup_id": backup_id,
            "backup_dir": str(backup_dir),
            "manifest_path": str(backup_dir / "manifest.json"),
            "created_at": created_at,
            "patch_run_id": str(patch_run_id or ""),
            "repo_root": str(Path(repo_root).resolve()),
            "branch": str(branch or ""),
            "source_commit": str(source_commit or ""),
            "target_commit": str(target_commit or ""),
            "reason": str(reason or ""),
            "file_count": len(files),
            "total_size_bytes": total_size_bytes,
            "files": files,
        }
        self._write_manifest(backup_dir, manifest)
        return manifest

    def restore_backup(self, *, manifest: Mapping[str, Any]) -> Dict[str, Any]:
        """Restore one previously created backup manifest into the runtime data dir."""

        verification = self.verify_backup_manifest(manifest=manifest)
        if not bool(verification.get("ok")):
            raise RuntimeError(str(verification.get("summary") or "백업 검증에 실패했습니다."))
        restored_files: list[Dict[str, Any]] = []
        for item in list(verification.get("files") or []):
            if not isinstance(item, dict):
                continue
            relative_raw = str(item.get("relative_path") or "").strip()
            destination_raw = str(item.get("runtime_destination_path") or "").strip()
            backup_source_raw = str(item.get("backup_source_path") or "").strip()
            if not relative_raw or not destination_raw or not backup_source_raw:
                continue
            source_path = Path(backup_source_raw)
            destination_path = Path(destination_raw)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
            restored_files.append(
                {
                    "relative_path": relative_raw,
                    "destination_path": str(destination_path),
                    "size_bytes": int(destination_path.stat().st_size),
                }
            )

        return {
            "backup_id": str(manifest.get("backup_id") or ""),
            "restored_at": self.utc_now_iso(),
            "restored_file_count": len(restored_files),
            "verification": verification,
            "files": restored_files,
        }

    def verify_backup_manifest(self, *, manifest: Mapping[str, Any]) -> Dict[str, Any]:
        """Verify one backup manifest exists on disk and all referenced files are available."""

        manifest_payload = self._normalized_manifest_payload(manifest)
        manifest_path_raw = str(manifest_payload.get("manifest_path") or "").strip()
        backup_id = str(manifest_payload.get("backup_id") or "").strip()
        backup_dir = str(manifest_payload.get("backup_dir") or "").strip()
        manifest_path = Path(manifest_path_raw) if manifest_path_raw else None
        manifest_exists = bool(manifest_path and manifest_path.exists() and manifest_path.is_file())
        verified_files: list[Dict[str, Any]] = []
        missing_files: list[str] = []

        for item in list(manifest_payload.get("files") or []):
            if not isinstance(item, dict):
                continue
            relative_raw = str(item.get("relative_path") or "").strip()
            runtime_destination_raw = str(item.get("source_path") or "").strip()
            backup_source_raw = str(item.get("destination_path") or "").strip()
            if not relative_raw or not runtime_destination_raw or not backup_source_raw:
                continue
            backup_source_path = Path(backup_source_raw)
            if not backup_source_path.exists() or not backup_source_path.is_file():
                missing_files.append(relative_raw)
                continue
            verified_files.append(
                {
                    "relative_path": relative_raw,
                    "runtime_destination_path": runtime_destination_raw,
                    "backup_source_path": str(backup_source_path),
                    "size_bytes": int(backup_source_path.stat().st_size),
                }
            )

        ok = bool(backup_id) and manifest_exists and bool(verified_files) and not missing_files
        if ok:
            summary = f"백업 검증 완료: {len(verified_files)}개 파일을 복원할 수 있습니다."
        elif not manifest_exists:
            summary = "백업 manifest 파일을 찾을 수 없습니다."
        elif missing_files:
            summary = f"백업 파일 누락: {', '.join(missing_files[:5])}"
        else:
            summary = "복원 가능한 백업 파일이 없습니다."

        return {
            "ok": ok,
            "backup_id": backup_id,
            "backup_dir": backup_dir,
            "manifest_path": manifest_path_raw,
            "manifest_exists": manifest_exists,
            "file_count": len(verified_files),
            "missing_files": missing_files,
            "files": verified_files,
            "summary": summary,
        }

    def _write_manifest(self, backup_dir: Path, manifest: Dict[str, Any]) -> None:
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _normalized_manifest_payload(self, manifest: Mapping[str, Any]) -> Dict[str, Any]:
        manifest_path_raw = str(manifest.get("manifest_path") or "").strip()
        manifest_path = Path(manifest_path_raw) if manifest_path_raw else None
        if manifest_path and manifest_path.exists() and manifest_path.is_file():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                return payload
        return dict(manifest)

    @staticmethod
    def _build_backup_id(patch_run_id: str, created_at: str) -> str:
        safe_created = str(created_at or "").replace(":", "").replace("+", "_").replace("-", "").replace(".", "")
        safe_created = safe_created.replace("T", "_")
        return f"{str(patch_run_id or 'patch')}-{safe_created}".strip("-")

    def _relative_backup_path(self, source_path: Path) -> Path:
        try:
            return source_path.resolve().relative_to(self.data_root.resolve())
        except ValueError:
            return Path("external") / source_path.name
