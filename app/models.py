"""Domain models for AgentHub jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class JobStatus(str, Enum):
    """High-level lifecycle state of a job."""

    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"
    DONE = "done"


class JobStage(str, Enum):
    """Fine-grained stage inside orchestration workflow."""

    QUEUED = "queued"
    PREPARE_REPO = "prepare_repo"
    READ_ISSUE = "read_issue"
    WRITE_SPEC = "write_spec"
    IDEA_TO_PRODUCT_BRIEF = "idea_to_product_brief"
    GENERATE_USER_FLOWS = "generate_user_flows"
    DEFINE_MVP_SCOPE = "define_mvp_scope"
    ARCHITECTURE_PLANNING = "architecture_planning"
    PROJECT_SCAFFOLDING = "project_scaffolding"
    PLAN_WITH_GEMINI = "plan_with_gemini"
    DESIGN_WITH_CODEX = "design_with_codex"
    COPYWRITER_TASK = "copywriter_task"
    DOCUMENTATION_TASK = "documentation_task"
    IMPLEMENT_WITH_CODEX = "implement_with_codex"
    SUMMARIZE_CODE_CHANGES = "summarize_code_changes"
    TEST_AFTER_IMPLEMENT = "test_after_implement"
    UX_E2E_REVIEW = "ux_e2e_review"
    COMMIT_IMPLEMENT = "commit_implement"
    REVIEW_WITH_GEMINI = "review_with_gemini"
    PRODUCT_REVIEW = "product_review"
    IMPROVEMENT_STAGE = "improvement_stage"
    FIX_WITH_CODEX = "fix_with_codex"
    TEST_AFTER_FIX = "test_after_fix"
    COMMIT_FIX = "commit_fix"
    PUSH_BRANCH = "push_branch"
    CREATE_PR = "create_pr"
    FINALIZE = "finalize"
    DONE = "done"
    FAILED = "failed"


@dataclass
class JobRecord:
    """Stored record for one issue automation job."""

    job_id: str
    repository: str
    issue_number: int
    issue_title: str
    issue_url: str
    status: str
    stage: str
    attempt: int
    max_attempts: int
    branch_name: str
    pr_url: Optional[str]
    error_message: Optional[str]
    log_file: str
    created_at: str
    updated_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    app_code: str = "default"
    track: str = "enhance"
    workflow_id: str = ""
    heartbeat_at: Optional[str] = None
    recovery_status: str = ""
    recovery_reason: str = ""
    recovery_count: int = 0
    last_recovered_at: Optional[str] = None
    manual_resume_mode: str = ""
    manual_resume_node_id: str = ""
    manual_resume_requested_at: Optional[str] = None
    manual_resume_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary for JSON storage."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "JobRecord":
        """Create a JobRecord from stored JSON data."""

        return cls(**payload)


@dataclass
class NodeRunRecord:
    """Stored workflow node execution record for one job attempt."""

    node_run_id: str
    job_id: str
    workflow_id: str
    node_id: str
    node_type: str
    node_title: str
    status: str
    attempt: int
    started_at: str
    finished_at: Optional[str] = None
    error_message: Optional[str] = None
    agent_profile: str = "primary"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary for JSON storage."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "NodeRunRecord":
        """Create a NodeRunRecord from stored JSON data."""

        return cls(**payload)



def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC.

    We always store UTC in JSON so logs and dashboards are consistent across
    servers and timezones.
    """

    return datetime.now(timezone.utc).isoformat()
