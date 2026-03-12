"""Recovery and test-gate runtime helpers."""

from __future__ import annotations

from pathlib import Path
import json
import hashlib
import os
import re
import time
from typing import Callable, Dict

from app.command_runner import CommandExecutionError
from app.failure_classification import build_failure_evidence_summary
from app.models import JobRecord, JobStage
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


class RecoveryRuntime:
    """Encapsulate failure analysis, recovery, and test-gate behavior."""

    def __init__(
        self,
        *,
        command_templates,
        stage_run_tests: Callable[..., bool],
        append_actor_log: Callable[[Path, str, str], None],
        stage_fix_with_codex: Callable[..., None],
        commit_markdown_changes_after_stage: Callable[[JobRecord, Path, str, Path], None],
        is_recovery_mode_enabled: Callable[[], bool],
        find_configured_template_for_route: Callable[[str], str | None],
        template_for_route: Callable[[str], str],
        build_template_variables,
        docs_file: Callable[[Path, str], Path],
        actor_log_writer,
        is_escalation_enabled: Callable[[], bool],
        run_optional_escalation: Callable[[str, Path, str], None],
        feature_enabled: Callable[[str], bool] | None = None,
        recovery_shadow_runner=None,
    ) -> None:
        self.command_templates = command_templates
        self.stage_run_tests = stage_run_tests
        self.append_actor_log = append_actor_log
        self.stage_fix_with_codex = stage_fix_with_codex
        self.commit_markdown_changes_after_stage = commit_markdown_changes_after_stage
        self.is_recovery_mode_enabled = is_recovery_mode_enabled
        self.find_configured_template_for_route = find_configured_template_for_route
        self.template_for_route = template_for_route
        self.build_template_variables = build_template_variables
        self.docs_file = docs_file
        self.actor_log_writer = actor_log_writer
        self.is_escalation_enabled = is_escalation_enabled
        self.run_optional_escalation = run_optional_escalation
        self.feature_enabled = feature_enabled or (lambda _flag_name: False)
        self.recovery_shadow_runner = recovery_shadow_runner

    def run_test_hard_gate(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        stage: JobStage,
        gate_label: str,
    ) -> None:
        """Run test gate with bounded retry/timebox and repeated-error detection."""

        max_attempts = self.hard_gate_max_attempts()
        timebox_seconds = self.hard_gate_timebox_seconds()
        start = time.monotonic()
        signatures: Dict[str, int] = {}
        selected_policy = None

        for attempt in range(1, max_attempts + 1):
            passed = self.stage_run_tests(
                job=job,
                repository_path=repository_path,
                stage=stage,
                log_path=log_path,
            )
            if passed:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[HARD_GATE:{gate_label}] passed on attempt {attempt}/{max_attempts}",
                )
                return

            signature = self.latest_test_failure_signature(repository_path, stage)
            failure_reason = self.latest_test_failure_reason(repository_path, stage) or (
                f"Hard gate failed at {gate_label}."
            )
            evidence = build_failure_evidence_summary(
                reason=failure_reason,
                stage=stage.value,
                source="recovery_runtime",
                error_message=failure_reason,
            )
            selected_policy = resolve_retry_policy(
                failure_class=str(evidence.get("failure_class", "")),
                provider_hint=str(evidence.get("provider_hint", "")),
                stage_family=str(evidence.get("stage_family", "")),
                default_retry_budget=max_attempts,
            )
            provider_hint = str(evidence.get("provider_hint", "")).strip()
            counter_snapshot = {}
            if should_track_provider_failure(provider_hint):
                counter_snapshot = record_provider_failure(
                    repository_path,
                    provider_hint=provider_hint,
                    failure_class=str(evidence.get("failure_class", "")).strip(),
                    stage_family=str(evidence.get("stage_family", "")).strip(),
                    reason_code=str(selected_policy.failure_class or evidence.get("failure_class", "")).strip(),
                    reason=failure_reason,
                    job_id=job.job_id,
                    attempt=int(job.attempt or 0),
                    occurrence_key=f"hard_gate:{gate_label}:{attempt}",
                )
            effective_attempt_budget = min(max_attempts, int(selected_policy.retry_budget or max_attempts))
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                (
                    f"[HARD_GATE:{gate_label}] retry policy "
                    f"class={selected_policy.failure_class} "
                    f"budget={effective_attempt_budget} "
                    f"path={selected_policy.recovery_path}"
                    f"{' needs_human' if selected_policy.needs_human_recommended else ''}"
                ),
            )
            cooldown = (
                evaluate_provider_cooldown(
                    provider_hint=provider_hint,
                    failure_class=str(evidence.get("failure_class", "")).strip(),
                    counter_snapshot=counter_snapshot,
                    retry_policy=selected_policy.to_dict(),
                )
                if counter_snapshot and not selected_policy.needs_human_recommended
                else {"active": False}
            )
            quarantine = (
                evaluate_provider_quarantine(
                    provider_hint=provider_hint,
                    failure_class=str(evidence.get("failure_class", "")).strip(),
                    counter_snapshot=counter_snapshot,
                )
                if counter_snapshot and not selected_policy.needs_human_recommended
                else {"active": False}
            )
            circuit_breaker = (
                evaluate_provider_circuit_breaker(
                    provider_hint=provider_hint,
                    failure_class=str(evidence.get("failure_class", "")).strip(),
                    counter_snapshot=counter_snapshot,
                )
                if counter_snapshot and not selected_policy.needs_human_recommended
                else {"active": False}
            )
            if signature:
                signatures[signature] = signatures.get(signature, 0) + 1

            elapsed = int(time.monotonic() - start)
            if elapsed >= timebox_seconds:
                self.run_failure_assistant(
                    job=job,
                    repository_path=repository_path,
                    log_path=log_path,
                    reason=(
                        f"Hard gate timeout at {gate_label} ({elapsed}s/{timebox_seconds}s). "
                        "Do not fail the run. Summarize root cause and next unblock actions."
                    ),
                )
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[SOFT_TIMEOUT:{gate_label}] timeout reached ({elapsed}s). Continuing workflow by policy.",
                )
                append_runtime_recovery_trace(
                    repository_path,
                    source="recovery_runtime",
                    reason_code="hard_gate_timeout",
                    reason=(
                        f"Hard gate timeout at {gate_label} ({elapsed}s/{timebox_seconds}s). "
                        "Workflow continues by policy."
                    ),
                    decision="continue_by_policy",
                    stage=stage.value,
                    gate_label=gate_label,
                    job_id=job.job_id,
                    attempt=int(job.attempt or 0),
                    recovery_status="continued_by_policy",
                    recovery_count=int(job.recovery_count or 0),
                    details={
                        "elapsed_seconds": elapsed,
                        "timebox_seconds": timebox_seconds,
                        "retry_policy": selected_policy.to_dict(),
                    },
                )
                return
            if circuit_breaker.get("active"):
                circuit_reason = format_provider_circuit_breaker_reason(circuit_breaker)
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[HARD_GATE:{gate_label}] provider circuit-breaker active: {circuit_reason}",
                )
                append_runtime_recovery_trace(
                    repository_path,
                    source="recovery_runtime",
                    reason_code=str(selected_policy.failure_class or evidence.get("failure_class", "")),
                    reason=circuit_reason,
                    decision="provider_circuit_open",
                    stage=stage.value,
                    gate_label=gate_label,
                    job_id=job.job_id,
                    attempt=int(job.attempt or 0),
                    recovery_status="provider_circuit_open",
                    recovery_count=int(job.recovery_count or 0),
                    details={
                        "retry_policy": {**selected_policy.to_dict(), "recovery_path": "provider_circuit_breaker"},
                        "provider_failure_counter": counter_snapshot,
                        "circuit_breaker": circuit_breaker,
                        "recommended_route_action": "alternate_provider_circuit_breaker_or_manual_handoff",
                    },
                )
                raise CommandExecutionError(
                    f"Hard gate '{gate_label}' stopped because {circuit_reason}. "
                    "Next action: keep this provider open-circuited and switch route or hand off."
                )

            if quarantine.get("active"):
                quarantine_reason = format_provider_quarantine_reason(quarantine)
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[HARD_GATE:{gate_label}] provider quarantine active: {quarantine_reason}",
                )
                append_runtime_recovery_trace(
                    repository_path,
                    source="recovery_runtime",
                    reason_code=str(selected_policy.failure_class or evidence.get("failure_class", "")),
                    reason=quarantine_reason,
                    decision="provider_quarantined",
                    stage=stage.value,
                    gate_label=gate_label,
                    job_id=job.job_id,
                    attempt=int(job.attempt or 0),
                    recovery_status="provider_quarantined",
                    recovery_count=int(job.recovery_count or 0),
                    details={
                        "retry_policy": {**selected_policy.to_dict(), "recovery_path": "provider_quarantine"},
                        "provider_failure_counter": counter_snapshot,
                        "quarantine": quarantine,
                        "recommended_route_action": "alternate_provider_or_manual_handoff",
                    },
                )
                raise CommandExecutionError(
                    f"Hard gate '{gate_label}' stopped because {quarantine_reason}. "
                    "Next action: switch provider route or hand off to operator."
                )
            if cooldown.get("active"):
                cooldown_reason = format_provider_cooldown_reason(cooldown)
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[HARD_GATE:{gate_label}] provider cooldown active: {cooldown_reason}",
                )
                append_runtime_recovery_trace(
                    repository_path,
                    source="recovery_runtime",
                    reason_code=str(selected_policy.failure_class or evidence.get("failure_class", "")),
                    reason=cooldown_reason,
                    decision="cooldown_wait",
                    stage=stage.value,
                    gate_label=gate_label,
                    job_id=job.job_id,
                    attempt=int(job.attempt or 0),
                    recovery_status="cooldown_wait",
                    recovery_count=int(job.recovery_count or 0),
                    details={
                        "retry_policy": selected_policy.to_dict(),
                        "provider_failure_counter": counter_snapshot,
                        "cooldown": cooldown,
                    },
                )
                raise CommandExecutionError(
                    f"Hard gate '{gate_label}' stopped because {cooldown_reason}. "
                    "Next action: wait for provider recovery or switch route."
                )
            if signature and signatures.get(signature, 0) >= 2:
                if self.is_recovery_mode_enabled():
                    recovered = self.try_recovery_flow(
                        job=job,
                        repository_path=repository_path,
                        paths=paths,
                        log_path=log_path,
                        stage=stage,
                        gate_label=gate_label,
                        reason=(
                            f"Hard gate repeated failure signature at {gate_label}. "
                            "Analyze recoverability and attempt one recovery cycle."
                        ),
                    )
                    if recovered:
                        return
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[RECOVERY_MODE:{gate_label}] not recovered. Continuing workflow by policy.",
                    )
                    return
                self.run_failure_assistant(
                    job=job,
                    repository_path=repository_path,
                    log_path=log_path,
                    reason=(
                        f"Hard gate repeated failure signature at {gate_label}. "
                        "Summarize root cause and concrete fix plan."
                    ),
                )
                raise CommandExecutionError(
                    f"Hard gate '{gate_label}' stopped due to repeated failure signature. "
                    "Next action: resolve root cause before retrying."
                )
            if not should_retry_attempt(attempt=attempt, retry_budget=effective_attempt_budget):
                break

            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[HARD_GATE:{gate_label}] failed attempt {attempt}/{effective_attempt_budget}. Running fix and retry.",
            )
            self.stage_fix_with_codex(job, repository_path, paths, log_path)
            self.commit_markdown_changes_after_stage(
                job,
                repository_path,
                JobStage.FIX_WITH_CODEX.value,
                log_path,
            )

        if selected_policy and selected_policy.needs_human_recommended:
            append_runtime_recovery_trace(
                repository_path,
                source="recovery_runtime",
                reason_code=str(selected_policy.failure_class or "unknown_runtime"),
                reason=(
                    f"Hard gate '{gate_label}' stopped by retry policy "
                    f"{selected_policy.failure_class}/{selected_policy.recovery_path}. "
                    "Next action: verify credentials/quota/workflow contract before retrying."
                ),
                decision="needs_human",
                stage=stage.value,
                gate_label=gate_label,
                job_id=job.job_id,
                attempt=int(job.attempt or 0),
                recovery_status="needs_human",
                recovery_count=int(job.recovery_count or 0),
                details={
                    "retry_policy": selected_policy.to_dict(),
                },
            )
            raise CommandExecutionError(
                f"Hard gate '{gate_label}' stopped by retry policy "
                f"{selected_policy.failure_class}/{selected_policy.recovery_path}. "
                "Next action: verify credentials/quota/workflow contract before retrying."
            )
        if self.is_recovery_mode_enabled():
            recovered = self.try_recovery_flow(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=stage,
                gate_label=gate_label,
                reason=(
                    f"Hard gate max attempts reached at {gate_label}. "
                    "Analyze recoverability and attempt one recovery cycle."
                ),
            )
            if recovered:
                return
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[RECOVERY_MODE:{gate_label}] not recovered. Continuing workflow by policy.",
            )
            return
        raise CommandExecutionError(
            f"Hard gate '{gate_label}' failed after {max_attempts} attempts. "
            "Next action: inspect test reports and apply targeted fix."
        )

    def run_test_gate_by_policy(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        stage: JobStage,
        gate_label: str,
        app_type: str,
    ) -> None:
        """Run hard/soft test gate by policy. Default keeps non-web as soft gate."""

        policy = (os.getenv("AGENTHUB_TEST_GATE_POLICY", "soft") or "soft").strip().lower()
        use_hard_gate = policy == "hard" or (policy == "mixed" and (app_type or "").strip().lower() == "web")
        if policy in {"soft", "continue"}:
            use_hard_gate = False

        if use_hard_gate:
            self.run_test_hard_gate(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=stage,
                gate_label=gate_label,
            )
            return

        passed = self.stage_run_tests(
            job=job,
            repository_path=repository_path,
            stage=stage,
            log_path=log_path,
        )
        if not passed:
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[SOFT_GATE:{gate_label}] test failed but continuing by policy.",
            )
            if self.is_recovery_mode_enabled():
                recovered = self.try_recovery_flow(
                    job=job,
                    repository_path=repository_path,
                    paths=paths,
                    log_path=log_path,
                    stage=stage,
                    gate_label=gate_label,
                    reason=(
                        f"Soft gate failure at {gate_label}. "
                        "Analyze recoverability and attempt one recovery cycle."
                    ),
                )
                if recovered:
                    return
            self.run_failure_assistant(
                job=job,
                repository_path=repository_path,
                log_path=log_path,
                reason=(
                    f"Soft gate failure at {gate_label}. Workflow continues by policy. "
                    "Analyze probable root cause and recommend next fixes."
                ),
            )

    def try_recovery_flow(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        stage: JobStage,
        gate_label: str,
        reason: str,
    ) -> bool:
        """Analyze recoverability and run one fix+retest cycle when worth trying."""

        self.run_failure_assistant(
            job=job,
            repository_path=repository_path,
            log_path=log_path,
            reason=reason,
        )
        analysis_written = self._failure_analysis_written(repository_path)
        recoverable = self.is_recoverable_failure(repository_path, stage)
        if not recoverable:
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[RECOVERY_MODE:{gate_label}] not recoverable by heuristic. Skip auto-recovery.",
            )
            self._write_recovery_shadow_trace(
                repository_path=repository_path,
                stage=stage,
                gate_label=gate_label,
                reason=reason,
                analysis_written=analysis_written,
                recoverable=False,
                recovery_attempted=False,
                recovery_succeeded=False,
            )
            append_runtime_recovery_trace(
                repository_path,
                source="recovery_runtime",
                reason_code="recovery_not_recoverable",
                reason=reason,
                decision="continue_by_policy",
                stage=stage.value,
                gate_label=gate_label,
                job_id=job.job_id,
                attempt=int(job.attempt or 0),
                recovery_status="continued_by_policy",
                recovery_count=int(job.recovery_count or 0),
                details={"analysis_written": analysis_written},
            )
            return False
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"[RECOVERY_MODE:{gate_label}] recoverable. Running fix + retest once.",
        )
        self.stage_fix_with_codex(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job,
            repository_path,
            JobStage.FIX_WITH_CODEX.value,
            log_path,
        )
        passed = self.stage_run_tests(
            job=job,
            repository_path=repository_path,
            stage=stage,
            log_path=log_path,
        )
        if passed:
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[RECOVERY_MODE:{gate_label}] recovery succeeded.",
            )
            self._write_recovery_shadow_trace(
                repository_path=repository_path,
                stage=stage,
                gate_label=gate_label,
                reason=reason,
                analysis_written=analysis_written,
                recoverable=True,
                recovery_attempted=True,
                recovery_succeeded=True,
            )
            append_runtime_recovery_trace(
                repository_path,
                source="recovery_runtime",
                reason_code="recovery_succeeded",
                reason=reason,
                decision="resume_workflow",
                stage=stage.value,
                gate_label=gate_label,
                job_id=job.job_id,
                attempt=int(job.attempt or 0),
                recovery_status="auto_recovered",
                recovery_count=int(job.recovery_count or 0),
                details={"analysis_written": analysis_written},
            )
            return True
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"[RECOVERY_MODE:{gate_label}] recovery attempt failed.",
        )
        self._write_recovery_shadow_trace(
            repository_path=repository_path,
            stage=stage,
            gate_label=gate_label,
            reason=reason,
            analysis_written=analysis_written,
            recoverable=True,
            recovery_attempted=True,
            recovery_succeeded=False,
        )
        append_runtime_recovery_trace(
            repository_path,
            source="recovery_runtime",
            reason_code="recovery_failed",
            reason=reason,
            decision="continue_by_policy",
            stage=stage.value,
            gate_label=gate_label,
            job_id=job.job_id,
            attempt=int(job.attempt or 0),
            recovery_status="continued_by_policy",
            recovery_count=int(job.recovery_count or 0),
            details={"analysis_written": analysis_written},
        )
        return False

    @staticmethod
    def is_recoverable_failure(repository_path: Path, stage: JobStage) -> bool:
        """Cheap heuristic for auto-recovery eligibility."""

        reason_path = repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md"
        report_path = repository_path / f"TEST_REPORT_{stage.value.upper()}.md"
        text = ""
        if reason_path.exists():
            text += "\n" + reason_path.read_text(encoding="utf-8", errors="replace")
        if report_path.exists():
            text += "\n" + report_path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        if any(
            token in lowered
            for token in [
                "auth",
                "permission denied",
                "rate limit",
                "quota",
                "repository not found",
                "dns",
                "network is unreachable",
            ]
        ):
            return False
        if any(
            token in lowered
            for token in [
                "test failed",
                "lint",
                "type error",
                "module not found",
                "assert",
                "failed",
            ]
        ):
            return True
        return bool(lowered.strip())

    def run_failure_assistant(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
        reason: str,
    ) -> None:
        """Run codex/escalation helper on failure and persist analysis markdown."""

        prompt_path = self.docs_file(repository_path, "FAILURE_ANALYSIS_PROMPT.md")
        output_path = self.docs_file(repository_path, "FAILURE_ANALYSIS.md")
        prompt_path.write_text(
            (
                "실패 원인 분석을 작성하세요.\n"
                "- 한국어\n"
                "- 재현 단서 3개 이내\n"
                "- 근본 원인(가설) 1~3개\n"
                "- 즉시 조치 3개(명령/파일 기준)\n"
                "- 다음 라운드 체크리스트\n\n"
                f"job_id: {job.job_id}\n"
                f"issue: #{job.issue_number}\n"
                f"reason: {reason}\n"
            ),
            encoding="utf-8",
        )

        if self.find_configured_template_for_route("codex_helper"):
            try:
                result = self.command_templates.run_template(
                    template_name=self.template_for_route("codex_helper"),
                    variables=self.build_template_variables(
                        job,
                        {
                            "spec": self.docs_file(repository_path, "SPEC.md"),
                            "plan": self.docs_file(repository_path, "PLAN.md"),
                            "review": self.docs_file(repository_path, "REVIEW.md"),
                            "design": self.docs_file(repository_path, "DESIGN_SYSTEM.md"),
                            "status": self.docs_file(repository_path, "STATUS.md"),
                        },
                        prompt_path,
                    ),
                    cwd=repository_path,
                    log_writer=self.actor_log_writer(log_path, "CODEX_HELPER"),
                )
                analysis = str(getattr(result, "stdout", "")).strip()
                if analysis:
                    output_path.write_text(analysis + "\n", encoding="utf-8")
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Failure analysis written: {output_path.name}",
                    )
                    return
            except Exception as error:  # noqa: BLE001
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Failure assistant failed: {error}",
                )

        if self.is_escalation_enabled() and self.find_configured_template_for_route("escalation"):
            self.run_optional_escalation(job.job_id, log_path, reason)

    @staticmethod
    def hard_gate_max_attempts() -> int:
        """Read hard-gate max attempts from env with safe bounds."""

        raw = (os.getenv("AGENTHUB_HARD_GATE_MAX_ATTEMPTS", "3") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 3
        return max(1, min(5, value))

    @staticmethod
    def hard_gate_timebox_seconds() -> int:
        """Read hard-gate timebox seconds from env with safe bounds."""

        raw = (os.getenv("AGENTHUB_HARD_GATE_TIMEBOX_SECONDS", "1200") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 1200
        return max(120, min(7200, value))

    def latest_test_failure_signature(self, repository_path: Path, stage: JobStage) -> str:
        """Build compact signature from latest failure reason text."""

        text = self.latest_test_failure_reason(repository_path, stage)
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", text).strip().lower()[:600]
        return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @staticmethod
    def latest_test_failure_reason(repository_path: Path, stage: JobStage) -> str:
        """Read the latest test failure reason text when available."""

        reason_path = repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md"
        if not reason_path.exists():
            return ""
        try:
            return reason_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def run_fix_retry_loop_after_test_failure(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Run codex_fix -> test_after_fix loop up to 3 rounds after E2E failure."""

        max_rounds = 3
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Entering fix/test retry loop after E2E failure. max_rounds={max_rounds}",
        )
        for round_index in range(1, max_rounds + 1):
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[FIX_LOOP] Round {round_index}/{max_rounds} start",
            )
            self.stage_fix_with_codex(job, repository_path, paths, log_path)
            self.commit_markdown_changes_after_stage(
                job,
                repository_path,
                JobStage.FIX_WITH_CODEX.value,
                log_path,
            )
            passed = self.stage_run_tests(
                job=job,
                repository_path=repository_path,
                stage=JobStage.TEST_AFTER_FIX,
                log_path=log_path,
            )
            self.commit_markdown_changes_after_stage(
                job,
                repository_path,
                JobStage.TEST_AFTER_FIX.value,
                log_path,
            )
            if passed:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[FIX_LOOP] Round {round_index} succeeded. Proceeding to review stage.",
                )
                return

        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            "[FIX_LOOP] Reached max rounds with remaining failures. Proceeding by policy.",
        )

    def _write_recovery_shadow_trace(
        self,
        *,
        repository_path: Path,
        stage: JobStage,
        gate_label: str,
        reason: str,
        analysis_written: bool,
        recoverable: bool,
        recovery_attempted: bool,
        recovery_succeeded: bool,
    ) -> None:
        """Persist optional LangGraph recovery shadow trace."""

        shadow_path = self.docs_file(repository_path, "LANGGRAPH_RECOVERY_SHADOW.json")
        if not self.feature_enabled("langgraph_recovery_shadow"):
            shadow_path.write_text(
                json.dumps(
                    {
                        "enabled": False,
                        "available": False,
                        "status": "disabled",
                        "detail": "feature_flag_disabled",
                        "framework": "langgraph",
                        "framework_version": "",
                        "generated_at": "",
                        "session_count": 0,
                        "sessions": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return
        if self.recovery_shadow_runner is None:
            return
        session_payload = self.recovery_shadow_runner.run(
            stage=stage.value,
            gate_label=gate_label,
            reason=reason,
            analysis_written=analysis_written,
            recoverable=recoverable,
            recovery_attempted=recovery_attempted,
            recovery_succeeded=recovery_succeeded,
        )
        existing = {}
        if shadow_path.exists():
            try:
                existing = json.loads(shadow_path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                existing = {}
        sessions = existing.get("sessions", []) if isinstance(existing.get("sessions"), list) else []
        sessions.append(session_payload)
        payload = {
            "enabled": True,
            "available": bool(session_payload.get("framework_version")) or session_payload.get("status") != "unavailable",
            "status": "completed",
            "detail": "shadow_trace_recorded",
            "framework": "langgraph",
            "framework_version": str(session_payload.get("framework_version", "")).strip(),
            "generated_at": str(session_payload.get("generated_at", "")).strip(),
            "session_count": len(sessions),
            "sessions": sessions[-12:],
        }
        shadow_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _failure_analysis_written(repository_path: Path) -> bool:
        output_path = repository_path / "_docs" / "FAILURE_ANALYSIS.md"
        if not output_path.exists():
            return False
        try:
            return bool(output_path.read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            return False
