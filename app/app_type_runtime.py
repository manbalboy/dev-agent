"""App type resolution and non-web UX stage helpers for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from app.models import JobRecord, JobStage


class AppTypeRuntime:
    """Encapsulate SPEC-driven app type lookup and non-web UX skip handling."""

    def __init__(
        self,
        *,
        docs_file,
        set_stage,
        append_actor_log,
    ) -> None:
        self.docs_file = docs_file
        self.set_stage = set_stage
        self.append_actor_log = append_actor_log

    def resolve_app_type(self, repository_path: Path, paths: Dict[str, Path]) -> str:
        """Resolve app_type from SPEC.json with a safe web fallback."""

        spec_json_path = paths.get("spec_json", self.docs_file(repository_path, "SPEC.json"))
        if isinstance(spec_json_path, Path) and spec_json_path.exists():
            try:
                payload = json.loads(spec_json_path.read_text(encoding="utf-8"))
                value = str(payload.get("app_type", "")).strip().lower()
                if value in {"web", "api", "cli", "app"}:
                    return value
            except Exception:  # noqa: BLE001
                pass
        return "web"

    def stage_skip_ux_review_for_non_web(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        *,
        app_type: str,
    ) -> None:
        """Write skip record when UX E2E stage is not applicable."""

        del paths
        self.set_stage(job.job_id, JobStage.UX_E2E_REVIEW, log_path)
        review_path = self.docs_file(repository_path, "UX_REVIEW.md")
        review_path.write_text(
            (
                "# UX REVIEW\n\n"
                "## Summary\n"
                f"- Stage: `{JobStage.UX_E2E_REVIEW.value}`\n"
                "- Verdict: `SKIPPED`\n"
                f"- Reason: `non-web app_type ({app_type})`\n\n"
                "## Next Action\n"
                "- non-web 타입은 UX 스크린샷 E2E를 수행하지 않습니다.\n"
                "- API/CLI 전용 검증 결과를 우선 확인하세요.\n"
            ),
            encoding="utf-8",
        )
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"ux_e2e_review skipped for app_type={app_type}",
        )
