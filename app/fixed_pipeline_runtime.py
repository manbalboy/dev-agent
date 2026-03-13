"""Legacy fixed pipeline runtime extracted from orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from app.models import JobRecord, JobStage


class FixedPipelineRuntime:
    """Run the legacy hard-coded pipeline when workflow execution is unavailable."""

    def __init__(
        self,
        *,
        stage_read_issue: Callable[[JobRecord, Path, Path], Any],
        commit_markdown_changes_after_stage: Callable[[JobRecord, Path, str, Path], None],
        stage_write_spec: Callable[[JobRecord, Path, Any, Path], Dict[str, Path]],
        stage_idea_to_product_brief: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_generate_user_flows: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_define_mvp_scope: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_architecture_planning: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_project_scaffolding: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_plan_with_gemini: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        snapshot_plan_variant: Callable[[Path, Dict[str, Path], str, Path], None],
        stage_design_with_codex: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_publish_with_codex: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_implement_with_codex: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_summarize_code_changes: Callable[[JobRecord, Path, Path], None],
        run_test_hard_gate: Callable[..., None],
        stage_commit: Callable[[JobRecord, Path, JobStage, Path, str], None],
        stage_review_with_gemini: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_product_review: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_improvement_stage: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_fix_with_codex: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_documentation_with_claude: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        stage_push_branch: Callable[[JobRecord, Path, Path], None],
        stage_create_pr: Callable[[JobRecord, Path, Dict[str, Path], Path], None],
        set_stage: Callable[[str, JobStage, Path], None],
    ) -> None:
        self.stage_read_issue = stage_read_issue
        self.commit_markdown_changes_after_stage = commit_markdown_changes_after_stage
        self.stage_write_spec = stage_write_spec
        self.stage_idea_to_product_brief = stage_idea_to_product_brief
        self.stage_generate_user_flows = stage_generate_user_flows
        self.stage_define_mvp_scope = stage_define_mvp_scope
        self.stage_architecture_planning = stage_architecture_planning
        self.stage_project_scaffolding = stage_project_scaffolding
        self.stage_plan_with_gemini = stage_plan_with_gemini
        self.snapshot_plan_variant = snapshot_plan_variant
        self.stage_design_with_codex = stage_design_with_codex
        self.stage_publish_with_codex = stage_publish_with_codex
        self.stage_implement_with_codex = stage_implement_with_codex
        self.stage_summarize_code_changes = stage_summarize_code_changes
        self.run_test_hard_gate = run_test_hard_gate
        self.stage_commit = stage_commit
        self.stage_review_with_gemini = stage_review_with_gemini
        self.stage_product_review = stage_product_review
        self.stage_improvement_stage = stage_improvement_stage
        self.stage_fix_with_codex = stage_fix_with_codex
        self.stage_documentation_with_claude = stage_documentation_with_claude
        self.stage_push_branch = stage_push_branch
        self.stage_create_pr = stage_create_pr
        self.set_stage = set_stage

    def run_fixed_pipeline(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        """Run the legacy hard-coded pipeline (fallback path)."""

        issue = self.stage_read_issue(job, repository_path, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.READ_ISSUE.value, log_path
        )
        paths = self.stage_write_spec(job, repository_path, issue, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.WRITE_SPEC.value, log_path
        )
        self.stage_idea_to_product_brief(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.IDEA_TO_PRODUCT_BRIEF.value, log_path
        )
        self.stage_generate_user_flows(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.GENERATE_USER_FLOWS.value, log_path
        )
        self.stage_define_mvp_scope(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.DEFINE_MVP_SCOPE.value, log_path
        )
        self.stage_architecture_planning(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.ARCHITECTURE_PLANNING.value, log_path
        )
        self.stage_project_scaffolding(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.PROJECT_SCAFFOLDING.value, log_path
        )

        self.stage_plan_with_gemini(job, repository_path, paths, log_path)
        self.snapshot_plan_variant(repository_path, paths, "general", log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.PLAN_WITH_GEMINI.value, log_path
        )
        self.stage_design_with_codex(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.DESIGN_WITH_CODEX.value, log_path
        )
        self.stage_publish_with_codex(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(job, repository_path, "publisher_task", log_path)
        self.stage_implement_with_codex(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.IMPLEMENT_WITH_CODEX.value, log_path
        )
        self.stage_summarize_code_changes(job, repository_path, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.SUMMARIZE_CODE_CHANGES.value, log_path
        )
        self.run_test_hard_gate(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_IMPLEMENT,
            gate_label="after_implement",
        )
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.TEST_AFTER_IMPLEMENT.value, log_path
        )
        self.stage_commit(job, repository_path, JobStage.COMMIT_IMPLEMENT, log_path, "feat")
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.COMMIT_IMPLEMENT.value, log_path
        )

        self.stage_review_with_gemini(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.REVIEW_WITH_GEMINI.value, log_path
        )
        self.stage_product_review(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.PRODUCT_REVIEW.value, log_path
        )
        self.stage_improvement_stage(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.IMPROVEMENT_STAGE.value, log_path
        )
        self.stage_fix_with_codex(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.FIX_WITH_CODEX.value, log_path
        )
        self.run_test_hard_gate(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label="after_fix",
        )
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.TEST_AFTER_FIX.value, log_path
        )
        self.stage_commit(job, repository_path, JobStage.COMMIT_FIX, log_path, "fix")
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.COMMIT_FIX.value, log_path
        )
        self.stage_documentation_with_claude(job, repository_path, paths, log_path)
        self.commit_markdown_changes_after_stage(
            job, repository_path, JobStage.DOCUMENTATION_TASK.value, log_path
        )

        self.stage_push_branch(job, repository_path, log_path)
        self.stage_create_pr(job, repository_path, paths, log_path)
        self.set_stage(job.job_id, JobStage.FINALIZE, log_path)
