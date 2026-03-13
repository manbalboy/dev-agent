"""Issue loading and SPEC generation runtime for orchestrator."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage
from app.workflow_resume import build_workflow_artifact_paths


class IssueSpecRuntime:
    """Encapsulate issue loading and SPEC generation stages."""

    def __init__(
        self,
        *,
        settings,
        set_stage: Callable[[str, JobStage, Path], None],
        run_shell,
        append_actor_log: Callable[[Path, str, str], None],
        issue_details_factory: Callable[..., Any],
        build_spec_markdown: Callable[..., str],
        build_spec_json: Callable[..., Dict[str, Any]],
        issue_reader: Callable[..., Dict[str, Any]],
        repo_context_reader: Callable[[Path], Dict[str, Any]],
        risk_policy_checker: Callable[[Dict[str, Any]], Dict[str, Any]],
        spec_schema_validator: Callable[[Dict[str, Any]], Dict[str, Any]],
        spec_rewriter: Callable[[Dict[str, Any], Dict[str, Any]], tuple[Dict[str, Any], List[Dict[str, Any]]]],
        write_stage_contracts_doc: Callable[[Path, Path], None],
        write_pipeline_analysis_doc: Callable[[Path, Path], None],
        update_job: Callable[..., object],
    ) -> None:
        self.settings = settings
        self.set_stage = set_stage
        self.run_shell = run_shell
        self.append_actor_log = append_actor_log
        self.issue_details_factory = issue_details_factory
        self.build_spec_markdown = build_spec_markdown
        self.build_spec_json = build_spec_json
        self.issue_reader = issue_reader
        self.repo_context_reader = repo_context_reader
        self.risk_policy_checker = risk_policy_checker
        self.spec_schema_validator = spec_schema_validator
        self.spec_rewriter = spec_rewriter
        self.write_stage_contracts_doc = write_stage_contracts_doc
        self.write_pipeline_analysis_doc = write_pipeline_analysis_doc
        self.update_job = update_job

    def stage_read_issue(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ):
        """Read canonical issue details from GitHub CLI."""

        self.set_stage(job.job_id, JobStage.READ_ISSUE, log_path)
        result = self.run_shell(
            command=(
                f"gh issue view {job.issue_number} --repo {shlex.quote(job.repository)} "
                "--json title,body,url,labels"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose="read issue",
        )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise CommandExecutionError(
                "Could not parse issue details from gh issue view output. "
                "Next action: run the same command manually and verify gh CLI auth."
            ) from error

        labels_payload = payload.get("labels", [])
        labels: List[str] = []
        if isinstance(labels_payload, list):
            for item in labels_payload:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                else:
                    name = str(item).strip()
                if name:
                    labels.append(name)

        return self.issue_details_factory(
            title=str(payload.get("title", job.issue_title)),
            body=str(payload.get("body", "")),
            url=str(payload.get("url", job.issue_url)),
            labels=tuple(labels),
        )

    def stage_write_spec(
        self,
        job: JobRecord,
        repository_path: Path,
        issue,
        log_path: Path,
    ) -> Dict[str, Path]:
        """Generate SPEC artifacts and quality metadata for one job."""

        self.set_stage(job.job_id, JobStage.WRITE_SPEC, log_path)
        paths = build_workflow_artifact_paths(repository_path)
        spec_path = paths["spec"]
        spec_json_path = paths["spec_json"]
        spec_quality_path = paths["spec_quality"]
        stage_contracts_path = paths["stage_contracts"]
        stage_contracts_json_path = paths["stage_contracts_json"]
        pipeline_analysis_path = paths["pipeline_analysis"]
        pipeline_analysis_json_path = paths["pipeline_analysis_json"]

        spec_content = self.build_spec_markdown(
            repository=job.repository,
            issue_number=job.issue_number,
            issue_url=issue.url,
            issue_title=issue.title,
            issue_body=issue.body,
            preview_host=self.settings.docker_preview_host,
            preview_port_start=self.settings.docker_preview_port_start,
            preview_port_end=self.settings.docker_preview_port_end,
            preview_cors_origins=self.settings.docker_preview_cors_origins,
        )
        spec_path.write_text(spec_content, encoding="utf-8")
        spec_json = self.build_spec_json(
            repository=job.repository,
            issue_number=job.issue_number,
            issue_url=issue.url,
            issue_title=issue.title,
            issue_body=issue.body,
        )
        issue_context = self.issue_reader(
            issue_title=issue.title,
            issue_body=issue.body,
            issue_url=issue.url,
        )
        repo_context = self.repo_context_reader(repository_path)
        risk_report = self.risk_policy_checker(spec_json)
        validation = self.spec_schema_validator(spec_json)
        rewrites: List[Dict[str, Any]] = []
        max_rewrite_rounds = 2
        for round_index in range(1, max_rewrite_rounds + 1):
            if validation.get("passed"):
                break
            revised, actions = self.spec_rewriter(spec_json, validation)
            if not actions:
                break
            rewrites.append(
                {
                    "round": round_index,
                    "actions": actions,
                    "reject_codes": validation.get("reject_codes", []),
                }
            )
            spec_json = revised
            validation = self.spec_schema_validator(spec_json)

        spec_json["_quality"] = {
            "validation": validation,
            "rewrites": rewrites,
            "risk_report": risk_report,
            "issue_context": {
                "keywords": issue_context.get("keywords", []),
                "line_count": issue_context.get("line_count", 0),
            },
            "repo_context": {
                "stack": repo_context.get("stack", []),
                "has_readme_excerpt": bool(repo_context.get("readme_excerpt", "")),
            },
        }
        spec_json_path.write_text(
            json.dumps(spec_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        spec_quality_path.write_text(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "issue_number": job.issue_number,
                    "validation": validation,
                    "rewrites": rewrites,
                    "risk_report": risk_report,
                    "issue_context": issue_context,
                    "repo_context": repo_context,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.append_actor_log(log_path, "ORCHESTRATOR", f"Wrote SPEC.md at {spec_path}")
        self.append_actor_log(log_path, "ORCHESTRATOR", f"Wrote SPEC.json at {spec_json_path}")
        self.append_actor_log(log_path, "ORCHESTRATOR", f"Wrote SPEC_QUALITY.json at {spec_quality_path}")
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            (
                "SPEC quality check: "
                f"passed={validation.get('passed')} score={validation.get('score')} "
                f"reject_codes={','.join(validation.get('reject_codes', [])) or '-'}"
            ),
        )
        if not bool(validation.get("passed")):
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "SPEC quality gate not passed, but continuing by non-blocking assist policy.",
            )
        self.write_stage_contracts_doc(stage_contracts_path, stage_contracts_json_path)
        self.write_pipeline_analysis_doc(pipeline_analysis_path, pipeline_analysis_json_path)
        self.update_job(
            job.job_id,
            issue_title=issue.title,
            issue_url=issue.url,
        )
        return paths
