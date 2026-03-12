"""Mobile app quality artifact helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models import JobRecord, JobStage, utc_now_iso
from app.workflow_resume import build_workflow_artifact_paths


class MobileQualityRuntime:
    """Write baseline mobile verification artifacts into the workspace docs."""

    def __init__(self, *, settings) -> None:
        self.settings = settings

    def write_mobile_app_checklist(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        stage: JobStage,
        test_results: List[Dict[str, Any]],
    ) -> Optional[Path]:
        """Persist one mobile checklist artifact when the workspace is an app target."""

        if self._read_app_type(repository_path) != "app":
            return None

        paths = build_workflow_artifact_paths(repository_path)
        target_path = paths["mobile_app_checklist"]
        runner_meta = self._read_app_runner_meta(job.app_code)
        verification_target = self._verification_target_from_mode(str(runner_meta.get("mode", "")).strip())
        result_items = self._normalize_test_results(test_results)
        overall_pass = bool(result_items) and all(item["passed"] for item in result_items)
        overall_label = "PASS" if overall_pass else "FAIL"

        lines: List[str] = [
            "# MOBILE APP CHECKLIST",
            "",
            f"- Generated At: `{utc_now_iso()}`",
            f"- Job ID: `{job.job_id}`",
            f"- Repository: `{job.repository}`",
            f"- App Code: `{job.app_code or '-'}`",
            f"- Verification Stage: `{stage.value}`",
            f"- Verification Result: `{overall_label}`",
            f"- Verification Target: `{verification_target}`",
            "",
            "## App Runner State",
            "",
            f"- Mode: `{str(runner_meta.get('mode', '')).strip() or 'unknown'}`",
            f"- State: `{self._runner_state(runner_meta)}`",
            f"- Command: `{str(runner_meta.get('command', '')).strip() or '-'}`",
            f"- PID: `{str(runner_meta.get('pid', '')).strip() or '-'}`",
            f"- Port: `{str(runner_meta.get('port', '')).strip() or '-'}`",
            f"- Updated At: `{str(runner_meta.get('updated_at', '')).strip() or '-'}`",
            "",
            "## Test Evidence",
            "",
        ]

        if result_items:
            for item in result_items:
                lines.extend(
                    [
                        f"- `{item['tester']}`: `{item['status']}` "
                        f"(exit `{item['exit_code']}`, `{item['duration_seconds']:.2f}s`) "
                        f"/ report `{item['report_name']}`",
                        f"  - Command: `{item['command']}`",
                    ]
                )
        else:
            lines.append("- 테스트 결과가 아직 기록되지 않았습니다.")

        lines.extend(
            [
                "",
                "## Mobile Checklist",
                "",
                f"- Emulator / Simulator target: `{verification_target}`",
                "- Safe area: `미검증 (수동 확인 필요)`",
                "- Keyboard overlap: `미검증 (수동 확인 필요)`",
                "- Loading state: `미검증 (수동 확인 필요)`",
                "- Empty state: `미검증 (수동 확인 필요)`",
                "- Error state: `미검증 (수동 확인 필요)`",
                "- Offline / network failure: `미검증 (수동 확인 필요)`",
                "",
                "## Notes",
                "",
                "- 이 artifact는 baseline 자동 기록입니다.",
                "- runner meta와 TEST_REPORT를 바탕으로 마지막 mobile 검증 상태를 요약합니다.",
                "- UX/safe-area/manual emulator 확인이 필요하면 다음 라운드에서 명시적으로 보강합니다.",
                "",
            ]
        )

        target_path.write_text("\n".join(lines), encoding="utf-8")
        return target_path

    @staticmethod
    def _read_app_type(repository_path: Path) -> str:
        spec_json_path = repository_path / "_docs" / "SPEC.json"
        if not spec_json_path.exists():
            return "web"
        try:
            payload = json.loads(spec_json_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return "web"
        value = str(payload.get("app_type", "")).strip().lower()
        return value or "web"

    def _read_app_runner_meta(self, app_code: str) -> Dict[str, Any]:
        safe_app_code = re.sub(r"[^a-zA-Z0-9_-]+", "", str(app_code or "").strip()) or "default"
        path = self.settings.data_dir / "pids" / f"app_{safe_app_code}.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _verification_target_from_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in {"expo-android", "rn-android"}:
            return "android_emulator"
        if normalized in {"expo-ios", "rn-ios"}:
            return "ios_simulator"
        if normalized == "web":
            return "web_runner"
        return "manual_or_unknown"

    @staticmethod
    def _runner_state(meta: Dict[str, Any]) -> str:
        try:
            pid = int(str(meta.get("pid", "")).strip())
        except (TypeError, ValueError):
            return "unknown"
        if pid <= 0:
            return "unknown"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "stopped"
        except PermissionError:
            return "running"
        except OSError:
            return "stopped"
        return "running"

    @staticmethod
    def _normalize_test_results(test_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in test_results:
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            report = item.get("report")
            tester_name = str(item.get("name", "")).strip() or "tester"
            exit_code = int(getattr(result, "exit_code", 1))
            normalized.append(
                {
                    "tester": tester_name,
                    "status": "PASS" if exit_code == 0 else "FAIL",
                    "passed": exit_code == 0,
                    "exit_code": exit_code,
                    "duration_seconds": float(getattr(result, "duration_seconds", 0.0) or 0.0),
                    "command": str(getattr(result, "command", "")).strip() or "-",
                    "report_name": Path(report).name if isinstance(report, Path) else str(report or "-"),
                }
            )
        return normalized
