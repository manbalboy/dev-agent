"""Job dispatch and single-attempt execution helpers for orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.command_runner import CommandExecutionError
from app.models import JobStage, JobStatus, utc_now_iso


class JobExecutionRuntime:
    """Encapsulate queue dispatch and single-attempt execution outside orchestrator."""

    def __init__(self, *, owner: Any) -> None:
        self.owner = owner

    def process_next_job(self) -> bool:
        """Pop one job from queue and process it."""

        job_id = self.owner.store.dequeue_job()
        if job_id is None:
            return False

        self.owner.process_job(job_id)
        return True

    def process_job(self, job_id: str) -> None:
        """Run one job with retry policy and final failure handling."""

        job = self.owner._require_job(job_id)
        log_path = self.owner.settings.logs_debug_dir / job.log_file
        self.owner._active_job_id = job_id
        self.owner._last_heartbeat_monotonic = 0.0
        self.owner._set_active_runtime_input_environment(job)
        self.owner._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Starting job {job.job_id} for issue #{job.issue_number}",
        )

        self.owner.store.update_job(
            job_id,
            status=JobStatus.RUNNING.value,
            stage=JobStage.QUEUED.value,
            started_at=job.started_at or utc_now_iso(),
            heartbeat_at=utc_now_iso(),
            error_message=None,
        )
        self.owner._touch_job_heartbeat(force=True)

        try:
            if self.owner._is_ultra10_track(job):
                self.process_ultra_job(job_id, log_path, max_runtime_hours=10, mode_tag="ULTRA10")
                return
            if self.owner._is_ultra_track(job):
                self.process_ultra_job(job_id, log_path)
                return
            if self.owner._is_long_track(job):
                self.process_long_job(job_id, log_path)
                return

            self.owner._job_failure_runtime.run_standard_attempt_loop(job_id, log_path)
        finally:
            self.owner._active_job_id = None
            self.owner._last_heartbeat_monotonic = 0.0
            self.owner._active_runtime_input_env = {}
            self.owner._install_command_template_heartbeat()

    def process_long_job(self, job_id: str, log_path: Path) -> None:
        """Run long-track mode with fixed 3 rounds of full workflow."""

        self.owner._job_failure_runtime.process_long_job(job_id, log_path)

    def process_ultra_job(
        self,
        job_id: str,
        log_path: Path,
        *,
        max_runtime_hours: int = 5,
        mode_tag: str = "ULTRA",
    ) -> None:
        """Run ultra-long mode with round loop and graceful stop."""

        self.owner._job_failure_runtime.process_ultra_job(
            job_id,
            log_path,
            max_runtime_hours=max_runtime_hours,
            mode_tag=mode_tag,
        )

    def run_single_attempt(self, job_id: str, log_path: Path) -> None:
        """Execute one attempt with workflow-config first, fixed flow fallback."""

        job = self.owner._require_job(job_id)
        repository_path = self.owner._stage_prepare_repo(job, log_path)
        workflow = self.owner._load_active_workflow(job, log_path)
        if workflow is None:
            self.owner._run_fixed_pipeline(job, repository_path, log_path)
            return

        ordered_nodes = self.owner._linearize_workflow_nodes(workflow)
        if not ordered_nodes:
            raise CommandExecutionError("Workflow has no executable nodes.")
        resume_state = self.owner._resolve_workflow_resume_state(
            job=job,
            repository_path=repository_path,
            workflow=workflow,
            ordered_nodes=ordered_nodes,
        )

        self.owner._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Using workflow '{workflow.get('workflow_id', 'unknown')}'",
        )
        if resume_state["mode"] == "resume":
            skipped_count = len(resume_state.get("skipped_nodes", []))
            self.owner._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                (
                    "Workflow resume active: "
                    f"attempt={resume_state['current_attempt']} "
                    f"source_attempt={resume_state['source_attempt']} "
                    f"from={resume_state['resume_from_node_id']} "
                    f"({resume_state['resume_from_node_type']}) "
                    f"skipped={skipped_count}"
                ),
            )
        elif int(resume_state.get("source_attempt", 0)) > 0:
            self.owner._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow resume skipped: {resume_state.get('reason_code', 'full_rerun')}",
            )
        self.owner._run_workflow_pipeline(
            job,
            repository_path,
            workflow,
            ordered_nodes,
            log_path,
            resume_state=resume_state,
        )
