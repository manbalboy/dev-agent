"""Failure transition runtime for orchestrator retry/final-failure flows."""

from __future__ import annotations

from pathlib import Path
import shlex
import time
from typing import Any, Callable, Dict, Optional

from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.prompt_builder import build_status_markdown
from app.failure_classification import build_failure_evidence_summary
from app.provider_failure_counter_runtime import (
    evaluate_provider_circuit_breaker,
    evaluate_provider_cooldown,
    evaluate_provider_quarantine,
    format_provider_circuit_breaker_reason,
    format_provider_cooldown_reason,
    format_provider_quarantine_reason,
    record_provider_failure,
    should_track_provider_failure,
)
from app.retry_policy import resolve_retry_policy, should_retry_attempt
from app.runtime_recovery_trace import append_runtime_recovery_trace


class JobFailureRuntime:
    """Encapsulate retry loops, escalation, and final failure cleanup."""

    def __init__(
        self,
        *,
        settings,
        store,
        command_templates,
        require_job: Callable[[str], JobRecord],
        run_single_attempt: Callable[[str, Path], None],
        touch_job_heartbeat: Callable[..., None],
        append_actor_log: Callable[[Path, str, str], None],
        is_escalation_enabled: Callable[[], bool],
        find_configured_template_for_route: Callable[[str], Optional[str]],
        docs_file: Callable[[Path, str], Path],
        job_workspace_path: Callable[[JobRecord], Path],
        build_template_variables: Callable[[JobRecord, Dict[str, Path], Path], Dict[str, str]],
        template_for_route: Callable[[str], str],
        actor_log_writer: Callable[[Path, str], Callable[[str], None]],
        set_stage: Callable[[str, JobStage, Path], None],
        issue_reference_line: Callable[[JobRecord], str],
        run_shell: Callable[..., Any],
        push_branch_with_recovery: Callable[..., None],
        job_execution_repository: Callable[[JobRecord], str],
        get_pr_url: Callable[[JobRecord, Path, Path, Optional[object]], Optional[str]],
        is_stop_requested: Callable[[str], bool],
        clear_stop_requested: Callable[[str], None],
        set_agent_profile: Callable[[str], None],
    ) -> None:
        self.settings = settings
        self.store = store
        self.command_templates = command_templates
        self.require_job = require_job
        self.run_single_attempt = run_single_attempt
        self.touch_job_heartbeat = touch_job_heartbeat
        self.append_actor_log = append_actor_log
        self.is_escalation_enabled = is_escalation_enabled
        self.find_configured_template_for_route = find_configured_template_for_route
        self.docs_file = docs_file
        self.job_workspace_path = job_workspace_path
        self.build_template_variables = build_template_variables
        self.template_for_route = template_for_route
        self.actor_log_writer = actor_log_writer
        self.set_stage = set_stage
        self.issue_reference_line = issue_reference_line
        self.run_shell = run_shell
        self.push_branch_with_recovery = push_branch_with_recovery
        self.job_execution_repository = job_execution_repository
        self.get_pr_url = get_pr_url
        self.is_stop_requested = is_stop_requested
        self.clear_stop_requested = clear_stop_requested
        self.set_agent_profile = set_agent_profile

    def run_standard_attempt_loop(self, job_id: str, log_path: Path) -> None:
        """Run the normal retry loop and finalize failure when retries exhaust."""

        job = self.require_job(job_id)
        last_error: Optional[str] = None
        initial_attempt = max(1, int(job.attempt or 0) + 1)
        for attempt in range(initial_attempt, job.max_attempts + 1):
            self.store.update_job(
                job_id,
                attempt=attempt,
                error_message=None,
                heartbeat_at=utc_now_iso(),
            )
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Attempt {attempt}/{job.max_attempts} started",
            )
            self.touch_job_heartbeat(force=True)

            try:
                self.run_single_attempt(job_id, log_path)
                self._mark_job_done(job_id)
                self.append_actor_log(log_path, "ORCHESTRATOR", "Job finished successfully")
                return
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
                self.store.update_job(
                    job_id,
                    error_message=last_error,
                    heartbeat_at=utc_now_iso(),
                )
                refreshed_job = self.require_job(job_id)
                evidence = build_failure_evidence_summary(
                    reason=last_error,
                    stage=str(refreshed_job.stage or ""),
                    source="job_failure_runtime",
                    error_message=last_error,
                )
                retry_policy = resolve_retry_policy(
                    failure_class=str(evidence.get("failure_class", "")),
                    provider_hint=str(evidence.get("provider_hint", "")),
                    stage_family=str(evidence.get("stage_family", "")),
                    default_retry_budget=int(job.max_attempts or 1),
                )
                provider_hint = str(evidence.get("provider_hint", "")).strip()
                upstream_provider_guard = (
                    "retry policy provider_" in last_error.lower()
                    or "cooldown active" in last_error.lower()
                )
                counter_snapshot: Dict[str, Any] = {}
                if should_track_provider_failure(provider_hint) and not upstream_provider_guard:
                    counter_snapshot = record_provider_failure(
                        self.job_workspace_path(refreshed_job),
                        provider_hint=provider_hint,
                        failure_class=str(evidence.get("failure_class", "")).strip(),
                        stage_family=str(evidence.get("stage_family", "")).strip(),
                        reason_code=str(evidence.get("failure_class", "")).strip(),
                        reason=last_error,
                        job_id=refreshed_job.job_id,
                        attempt=int(refreshed_job.attempt or 0),
                    )
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Attempt {attempt} failed: {last_error}",
                )
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    (
                        "Retry policy selected: "
                        f"class={retry_policy.failure_class} "
                        f"budget={retry_policy.retry_budget} "
                        f"path={retry_policy.recovery_path}"
                        f"{' needs_human' if retry_policy.needs_human_recommended else ''}"
                    ),
                )
                cooldown = (
                    evaluate_provider_cooldown(
                        provider_hint=provider_hint,
                        failure_class=str(evidence.get("failure_class", "")).strip(),
                        counter_snapshot=counter_snapshot,
                        retry_policy=retry_policy.to_dict(),
                    )
                    if counter_snapshot and not retry_policy.needs_human_recommended
                    else {"active": False}
                )
                quarantine = (
                    evaluate_provider_quarantine(
                        provider_hint=provider_hint,
                        failure_class=str(evidence.get("failure_class", "")).strip(),
                        counter_snapshot=counter_snapshot,
                    )
                    if counter_snapshot and not retry_policy.needs_human_recommended
                    else {"active": False}
                )
                circuit_breaker = (
                    evaluate_provider_circuit_breaker(
                        provider_hint=provider_hint,
                        failure_class=str(evidence.get("failure_class", "")).strip(),
                        counter_snapshot=counter_snapshot,
                    )
                    if counter_snapshot and not retry_policy.needs_human_recommended
                    else {"active": False}
                )

                if self._should_run_escalation():
                    self.run_optional_escalation(job_id, log_path, last_error)

                if circuit_breaker.get("active"):
                    circuit_reason = format_provider_circuit_breaker_reason(circuit_breaker)
                    self.store.update_job(
                        job_id,
                        recovery_status="provider_circuit_open",
                        recovery_reason=circuit_reason,
                    )
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        (
                            "Provider circuit-breaker active: "
                            f"provider={circuit_breaker.get('provider_hint', '')} "
                            f"class={circuit_breaker.get('failure_class', '')} "
                            f"count={circuit_breaker.get('recent_failure_count', 0)}/"
                            f"{circuit_breaker.get('threshold', 0)}"
                        ),
                    )
                    append_runtime_recovery_trace(
                        self.job_workspace_path(refreshed_job),
                        source="job_failure_runtime",
                        reason_code=str(retry_policy.failure_class or evidence.get("failure_class", "")),
                        reason=circuit_reason,
                        decision="provider_circuit_open",
                        stage=str(refreshed_job.stage or ""),
                        job_id=refreshed_job.job_id,
                        attempt=int(refreshed_job.attempt or 0),
                        recovery_status="provider_circuit_open",
                        recovery_count=int(refreshed_job.recovery_count or 0),
                        details={
                            "retry_policy": {**retry_policy.to_dict(), "recovery_path": "provider_circuit_breaker"},
                            "error_message": last_error,
                            "provider_failure_counter": counter_snapshot,
                            "circuit_breaker": circuit_breaker,
                            "recommended_route_action": "alternate_provider_circuit_breaker_or_manual_handoff",
                        },
                    )
                    break

                if quarantine.get("active"):
                    quarantine_reason = format_provider_quarantine_reason(quarantine)
                    self.store.update_job(
                        job_id,
                        recovery_status="provider_quarantined",
                        recovery_reason=quarantine_reason,
                    )
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        (
                            "Provider quarantine active: "
                            f"provider={quarantine.get('provider_hint', '')} "
                            f"class={quarantine.get('failure_class', '')} "
                            f"count={quarantine.get('recent_failure_count', 0)}/"
                            f"{quarantine.get('threshold', 0)}"
                        ),
                    )
                    append_runtime_recovery_trace(
                        self.job_workspace_path(refreshed_job),
                        source="job_failure_runtime",
                        reason_code=str(retry_policy.failure_class or evidence.get("failure_class", "")),
                        reason=quarantine_reason,
                        decision="provider_quarantined",
                        stage=str(refreshed_job.stage or ""),
                        job_id=refreshed_job.job_id,
                        attempt=int(refreshed_job.attempt or 0),
                        recovery_status="provider_quarantined",
                        recovery_count=int(refreshed_job.recovery_count or 0),
                        details={
                            "retry_policy": {**retry_policy.to_dict(), "recovery_path": "provider_quarantine"},
                            "error_message": last_error,
                            "provider_failure_counter": counter_snapshot,
                            "quarantine": quarantine,
                            "recommended_route_action": "alternate_provider_or_manual_handoff",
                        },
                    )
                    break

                if "cooldown active" in last_error.lower():
                    self.store.update_job(
                        job_id,
                        recovery_status="cooldown_wait",
                        recovery_reason=last_error,
                    )
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Provider cooldown propagated from stage runtime: {last_error}",
                    )
                    break

                if cooldown.get("active"):
                    cooldown_reason = format_provider_cooldown_reason(cooldown)
                    self.store.update_job(
                        job_id,
                        recovery_status="cooldown_wait",
                        recovery_reason=cooldown_reason,
                    )
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        (
                            "Provider cooldown active: "
                            f"provider={cooldown.get('provider_hint', '')} "
                            f"class={cooldown.get('failure_class', '')} "
                            f"remaining={cooldown.get('remaining_seconds', 0)}s "
                            f"count={cooldown.get('recent_failure_count', 0)}/"
                            f"{cooldown.get('threshold', 0)}"
                        ),
                    )
                    if "cooldown active" not in last_error.lower():
                        append_runtime_recovery_trace(
                            self.job_workspace_path(refreshed_job),
                            source="job_failure_runtime",
                            reason_code=str(retry_policy.failure_class or evidence.get("failure_class", "")),
                            reason=cooldown_reason,
                            decision="cooldown_wait",
                            stage=str(refreshed_job.stage or ""),
                            job_id=refreshed_job.job_id,
                            attempt=int(refreshed_job.attempt or 0),
                            recovery_status="cooldown_wait",
                            recovery_count=int(refreshed_job.recovery_count or 0),
                            details={
                                "retry_policy": retry_policy.to_dict(),
                                "error_message": last_error,
                                "provider_failure_counter": counter_snapshot,
                                "cooldown": cooldown,
                            },
                        )
                    break

                if should_retry_attempt(attempt=attempt, retry_budget=retry_policy.retry_budget):
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        "Retrying with a fresh attempt.",
                    )
                else:
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        "Retry budget exhausted for current failure class. Finalizing as failed.",
                    )
                    if retry_policy.needs_human_recommended:
                        self.store.update_job(
                            job_id,
                            recovery_status="needs_human",
                            recovery_reason=(
                                f"{retry_policy.failure_class} -> {retry_policy.recovery_path}"
                            ),
                        )
                        append_runtime_recovery_trace(
                            self.job_workspace_path(refreshed_job),
                            source="job_failure_runtime",
                            reason_code=str(retry_policy.failure_class or ""),
                            reason=last_error,
                            decision="needs_human",
                            stage=str(refreshed_job.stage or ""),
                            job_id=refreshed_job.job_id,
                            attempt=int(refreshed_job.attempt or 0),
                            recovery_status="needs_human",
                            recovery_count=int(refreshed_job.recovery_count or 0),
                            details={
                                "retry_policy": retry_policy.to_dict(),
                                "error_message": last_error,
                            },
                        )
                    break

        self.finalize_failed_job(job_id, log_path, last_error or "Unknown error")

    def process_long_job(self, job_id: str, log_path: Path) -> None:
        """Run long-track mode with fixed round count."""

        total_rounds = 3
        last_error: Optional[str] = None
        job = self.require_job(job_id)
        start_round = max(1, int(job.attempt or 0) + 1)
        for round_index in range(start_round, total_rounds + 1):
            self.store.update_job(
                job_id,
                attempt=round_index,
                error_message=None,
                heartbeat_at=utc_now_iso(),
            )
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[LONG] Round {round_index}/{total_rounds} started",
            )
            self.touch_job_heartbeat(force=True)
            try:
                self.run_single_attempt(job_id, log_path)
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[LONG] Round {round_index}/{total_rounds} completed",
                )
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
                self.store.update_job(
                    job_id,
                    error_message=last_error,
                    heartbeat_at=utc_now_iso(),
                )
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[LONG] Round {round_index}/{total_rounds} failed: {last_error}",
                )
                if self._should_run_escalation():
                    self.run_optional_escalation(job_id, log_path, last_error)
                self.finalize_failed_job(job_id, log_path, last_error)
                return

        self._mark_job_done(job_id)
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"[LONG] Completed all {total_rounds} rounds successfully",
        )

    def process_ultra_job(
        self,
        job_id: str,
        log_path: Path,
        *,
        max_runtime_hours: int = 5,
        mode_tag: str = "ULTRA",
    ) -> None:
        """Run ultra-long mode with fallback-agent recovery."""

        job = self.require_job(job_id)
        ultra_started = time.monotonic()
        round_index = int(job.attempt or 0)
        max_runtime_seconds = max_runtime_hours * 60 * 60

        while True:
            elapsed = time.monotonic() - ultra_started
            if elapsed >= max_runtime_seconds:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"{mode_tag} mode max runtime ({max_runtime_hours}h) reached. "
                    "Finishing after current rounds.",
                )
                self._mark_job_done(job_id)
                return

            if self.is_stop_requested(job_id):
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Stop requested before next round. Finishing ultra job.",
                )
                self.clear_stop_requested(job_id)
                self._mark_job_done(job_id)
                return

            round_index += 1
            self.store.update_job(
                job_id,
                attempt=round_index,
                error_message=None,
                heartbeat_at=utc_now_iso(),
            )
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[{mode_tag}] Round {round_index} started",
            )
            self.touch_job_heartbeat(force=True)

            try:
                self.set_agent_profile("primary")
                self.run_single_attempt(job_id, log_path)
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[{mode_tag}] Round {round_index} completed with primary agents.",
                )
            except Exception as primary_error:  # noqa: BLE001
                last_error = str(primary_error)
                self.store.update_job(
                    job_id,
                    error_message=last_error,
                    heartbeat_at=utc_now_iso(),
                )
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[{mode_tag}] Primary agents failed in round {round_index}: {last_error}",
                )

                if self._should_run_escalation():
                    self.run_optional_escalation(job_id, log_path, last_error)

                try:
                    self.set_agent_profile("fallback")
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Trying fallback agents for round {round_index}.",
                    )
                    self.run_single_attempt(job_id, log_path)
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Round {round_index} recovered by fallback agents.",
                    )
                except Exception as fallback_error:  # noqa: BLE001
                    last_error = str(fallback_error)
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Fallback agents also failed in round {round_index}: {last_error}",
                    )
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Two-agent failure reached. Ending this ultra job.",
                    )
                    self.set_agent_profile("primary")
                    self.finalize_failed_job(job_id, log_path, last_error)
                    return
                finally:
                    self.set_agent_profile("primary")

            if self.is_stop_requested(job_id):
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[{mode_tag}] Stop requested. Ending after round {round_index}.",
                )
                self.clear_stop_requested(job_id)
                self._mark_job_done(job_id, include_heartbeat=False)
                return

    def run_optional_escalation(self, job_id: str, log_path: Path, last_error: str) -> None:
        """Run optional escalation template after a failure."""

        job = self.require_job(job_id)
        repository_path = self.job_workspace_path(job)
        if not repository_path.exists():
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Escalation skipped because repository directory is not ready yet.",
            )
            return

        escalation_prompt_path = self.docs_file(repository_path, "ESCALATION_PROMPT.md")
        escalation_prompt_path.write_text(
            (
                "The main loop failed. Provide a short unblock plan.\n\n"
                f"Last error:\n{last_error}\n"
            ),
            encoding="utf-8",
        )

        self.append_actor_log(log_path, "ORCHESTRATOR", "Running optional escalation template.")
        try:
            self.command_templates.run_template(
                template_name=self.template_for_route("escalation"),
                variables={
                    **self.build_template_variables(
                        job,
                        {
                            "spec": self.docs_file(repository_path, "SPEC.md"),
                            "plan": self.docs_file(repository_path, "PLAN.md"),
                            "review": self.docs_file(repository_path, "REVIEW.md"),
                            "design": self.docs_file(repository_path, "DESIGN_SYSTEM.md"),
                            "status": self.docs_file(repository_path, "STATUS.md"),
                        },
                        escalation_prompt_path,
                    ),
                    "last_error": last_error,
                },
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "ESCALATION"),
            )
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(log_path, "ORCHESTRATOR", f"Escalation template failed: {error}")

    def finalize_failed_job(self, job_id: str, log_path: Path, last_error: str) -> None:
        """Best-effort cleanup when all retries are exhausted."""

        job = self.require_job(job_id)
        repository_path = self.job_workspace_path(job)
        existing_recovery_status = str(job.recovery_status or "").strip()
        self.set_stage(job_id, JobStage.FAILED, log_path)

        if repository_path.exists():
            status_path = self.docs_file(repository_path, "STATUS.md")
            status_path.write_text(
                build_status_markdown(
                    last_error=last_error,
                    next_actions=[
                        "Check failed command in job log and reproduce locally.",
                        "Fix root cause, then rerun by re-labeling issue with agent:run.",
                        "If needed, enable escalation template for extra guidance.",
                    ],
                ),
                encoding="utf-8",
            )
            self.append_actor_log(log_path, "ORCHESTRATOR", f"Wrote failure status file at {status_path}")
            self.try_create_wip_pr(job, repository_path, log_path)

        recovery_status = existing_recovery_status
        recovery_reason = str(job.recovery_reason or last_error or "").strip()
        if existing_recovery_status not in {
            "needs_human",
            "cooldown_wait",
            "provider_quarantined",
            "provider_circuit_open",
            "manual_resume_queued",
            "manual_rerun_queued",
            "auto_recovered",
        }:
            recovery_status = "dead_letter"
            recovery_reason = (
                "dead-letter after retry budget exhausted: "
                f"{str(last_error or '').strip()}"
            ).strip()
            append_runtime_recovery_trace(
                repository_path,
                source="job_failure_runtime",
                reason_code="dead_letter",
                reason=recovery_reason,
                decision="dead_letter",
                stage=JobStage.FAILED.value,
                job_id=job.job_id,
                attempt=int(job.attempt or 0),
                recovery_status="dead_letter",
                recovery_count=int(job.recovery_count or 0),
                details={
                    "upstream_recovery_status": existing_recovery_status,
                    "error_message": last_error,
                },
            )

        self.store.update_job(
            job_id,
            status=JobStatus.FAILED.value,
            stage=JobStage.FAILED.value,
            error_message=last_error,
            recovery_status=recovery_status,
            recovery_reason=recovery_reason,
            finished_at=utc_now_iso(),
            heartbeat_at=utc_now_iso(),
        )

    def try_create_wip_pr(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        """Try to commit STATUS.md and open a draft PR after fatal failure."""

        try:
            status_result = self.run_shell(
                command=f"git -C {shlex.quote(str(repository_path))} status --porcelain",
                cwd=repository_path,
                log_path=log_path,
                purpose="git status before WIP PR",
            )
            if status_result.stdout.strip():
                self.run_shell(
                    command=f"git -C {shlex.quote(str(repository_path))} add -A",
                    cwd=repository_path,
                    log_path=log_path,
                    purpose="git add for WIP PR",
                )
                self.run_shell(
                    command=(
                        f"git -C {shlex.quote(str(repository_path))} commit -m "
                        f"{shlex.quote(f'chore: add failure status for issue #{job.issue_number}')}"
                    ),
                    cwd=repository_path,
                    log_path=log_path,
                    purpose="git commit for WIP PR",
                )

            self.push_branch_with_recovery(
                repository_path=repository_path,
                branch_name=job.branch_name,
                log_path=log_path,
                purpose="push WIP branch",
            )

            wip_title = f"[WIP] AgentHub failed for issue #{job.issue_number}"
            wip_body = (
                "Automated run failed after max retries.\n\n"
                "Please check STATUS.md and job logs for next actions.\n"
                f"{self.issue_reference_line(job)}\n"
            )

            create_result = self.run_shell(
                command=(
                    f"gh pr create --draft --repo {shlex.quote(self.job_execution_repository(job))} "
                    f"--head {shlex.quote(job.branch_name)} "
                    f"--base {shlex.quote(self.settings.default_branch)} "
                    f"--title {shlex.quote(wip_title)} --body {shlex.quote(wip_body)}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="create WIP pull request",
            )

            pr_url = self.get_pr_url(job, repository_path, log_path, create_result)
            if pr_url:
                self.store.update_job(job.job_id, pr_url=pr_url)
                self.append_actor_log(log_path, "GITHUB", "Opened draft WIP PR after fatal failure.")
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(log_path, "GITHUB", f"WIP PR creation skipped: {error}")

    def _should_run_escalation(self) -> bool:
        return self.is_escalation_enabled() and bool(
            self.find_configured_template_for_route("escalation")
        )

    def _mark_job_done(self, job_id: str, *, include_heartbeat: bool = True) -> None:
        payload = {
            "status": JobStatus.DONE.value,
            "stage": JobStage.DONE.value,
            "finished_at": utc_now_iso(),
            "error_message": None,
        }
        if include_heartbeat:
            payload["heartbeat_at"] = utc_now_iso()
        self.store.update_job(job_id, **payload)
