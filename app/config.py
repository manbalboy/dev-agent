"""Application configuration for AgentHub.

This module intentionally keeps configuration simple and explicit so operators can
understand the system by reading environment variable names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings loaded from environment variables.

    The defaults are intentionally conservative for a local MVP setup.
    Production deployments should override secrets and directories via environment
    variables.
    """

    webhook_secret: str
    allowed_repository: str
    data_dir: Path
    workspace_dir: Path
    max_retries: int
    test_command: str
    test_command_secondary: str
    test_command_implement: str
    test_command_fix: str
    test_command_secondary_implement: str
    test_command_secondary_fix: str
    tester_primary_name: str
    tester_secondary_name: str
    command_config: Path
    worker_poll_seconds: int
    default_branch: str
    enable_escalation: bool
    enable_stage_md_commits: bool
    api_port: int
    store_backend: str
    sqlite_file: Path
    cors_allow_all: bool = True
    cors_origins: str = "*"
    docker_preview_enabled: bool = True
    docker_preview_host: str = "ssh.manbalboy.com"
    docker_preview_port_start: int = 7000
    docker_preview_port_end: int = 7099
    docker_preview_container_port: int = 3000
    docker_preview_health_path: str = "/"
    docker_preview_cors_origins: str = (
        "https://manbalboy.com,http://manbalboy.com,"
        "https://localhost,http://localhost,"
        "https://127.0.0.1,http://127.0.0.1"
    )

    @classmethod
    def from_env(cls) -> "AppSettings":
        """Build settings from environment variables.

        Raises:
            ValueError: If a required value is missing or malformed.
        """

        webhook_secret = os.getenv("AGENTHUB_WEBHOOK_SECRET", "")
        allowed_repository = os.getenv("AGENTHUB_ALLOWED_REPOSITORY", "")

        if not webhook_secret:
            raise ValueError(
                "AGENTHUB_WEBHOOK_SECRET is required. "
                "Set it to the same webhook secret configured in GitHub."
            )
        if not allowed_repository:
            raise ValueError(
                "AGENTHUB_ALLOWED_REPOSITORY is required (example: owner/repo)."
            )

        raw_data_dir = os.getenv("AGENTHUB_DATA_DIR", "data")
        raw_workspace_dir = os.getenv("AGENTHUB_WORKSPACE_DIR", "workspaces")
        raw_command_config = os.getenv(
            "AGENTHUB_COMMAND_CONFIG", "config/ai_commands.json"
        )

        max_retries = _read_int_env("AGENTHUB_MAX_RETRIES", default=3)
        worker_poll_seconds = _read_int_env("AGENTHUB_WORKER_POLL_SECONDS", default=5)
        test_command = os.getenv("AGENTHUB_TEST_COMMAND", "pytest -q")
        test_command_secondary = os.getenv("AGENTHUB_TEST_COMMAND_SECONDARY", test_command)
        test_command_implement = os.getenv("AGENTHUB_TEST_COMMAND_IMPLEMENT", test_command)
        test_command_fix = os.getenv("AGENTHUB_TEST_COMMAND_FIX", test_command)
        test_command_secondary_implement = os.getenv(
            "AGENTHUB_TEST_COMMAND_SECONDARY_IMPLEMENT",
            test_command_secondary,
        )
        test_command_secondary_fix = os.getenv(
            "AGENTHUB_TEST_COMMAND_SECONDARY_FIX",
            test_command_secondary,
        )
        tester_primary_name = os.getenv("AGENTHUB_TESTER_PRIMARY_NAME", "gpt").strip() or "gpt"
        tester_secondary_name = os.getenv("AGENTHUB_TESTER_SECONDARY_NAME", "gemini").strip() or "gemini"
        default_branch = os.getenv("AGENTHUB_DEFAULT_BRANCH", "main")
        api_port = _read_int_env("AGENTHUB_API_PORT", default=8321)
        enable_escalation = (
            os.getenv("AGENTHUB_ENABLE_ESCALATION", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        enable_stage_md_commits = (
            os.getenv("AGENTHUB_ENABLE_STAGE_MD_COMMITS", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        docker_preview_enabled = (
            os.getenv("AGENTHUB_DOCKER_PREVIEW_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        docker_preview_host = (
            os.getenv("AGENTHUB_DOCKER_PREVIEW_HOST", "ssh.manbalboy.com").strip()
            or "ssh.manbalboy.com"
        )
        docker_preview_port_start = _read_int_env("AGENTHUB_DOCKER_PREVIEW_PORT_START", default=7000)
        docker_preview_port_end = _read_int_env("AGENTHUB_DOCKER_PREVIEW_PORT_END", default=7099)
        if docker_preview_port_start > docker_preview_port_end:
            raise ValueError(
                "AGENTHUB_DOCKER_PREVIEW_PORT_START must be <= AGENTHUB_DOCKER_PREVIEW_PORT_END. "
                f"Current values: {docker_preview_port_start}, {docker_preview_port_end}"
            )
        docker_preview_container_port = _read_int_env("AGENTHUB_DOCKER_PREVIEW_CONTAINER_PORT", default=3000)
        docker_preview_health_path = (
            os.getenv("AGENTHUB_DOCKER_PREVIEW_HEALTH_PATH", "/").strip() or "/"
        )
        if not docker_preview_health_path.startswith("/"):
            docker_preview_health_path = "/" + docker_preview_health_path
        docker_preview_cors_origins = (
            os.getenv(
                "AGENTHUB_DOCKER_PREVIEW_CORS_ORIGINS",
                (
                    "https://manbalboy.com,http://manbalboy.com,"
                    "https://localhost,http://localhost,"
                    "https://127.0.0.1,http://127.0.0.1"
                ),
            ).strip()
            or (
                "https://manbalboy.com,http://manbalboy.com,"
                "https://localhost,http://localhost,"
                "https://127.0.0.1,http://127.0.0.1"
            )
        )
        store_backend = os.getenv("AGENTHUB_STORE_BACKEND", "json").strip().lower()
        if store_backend not in {"json", "sqlite"}:
            raise ValueError(
                "AGENTHUB_STORE_BACKEND must be 'json' or 'sqlite'. "
                f"Current value: {store_backend}"
            )
        raw_sqlite_file = os.getenv("AGENTHUB_SQLITE_FILE", str(Path(raw_data_dir) / "agenthub.db"))
        cors_allow_all = (
            os.getenv("AGENTHUB_CORS_ALLOW_ALL", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        cors_origins = os.getenv("AGENTHUB_CORS_ORIGINS", "*").strip() or "*"

        settings = cls(
            webhook_secret=webhook_secret,
            allowed_repository=allowed_repository,
            data_dir=Path(raw_data_dir).resolve(),
            workspace_dir=Path(raw_workspace_dir).resolve(),
            max_retries=max_retries,
            test_command=test_command,
            test_command_secondary=test_command_secondary,
            test_command_implement=test_command_implement,
            test_command_fix=test_command_fix,
            test_command_secondary_implement=test_command_secondary_implement,
            test_command_secondary_fix=test_command_secondary_fix,
            tester_primary_name=tester_primary_name,
            tester_secondary_name=tester_secondary_name,
            command_config=Path(raw_command_config).resolve(),
            worker_poll_seconds=worker_poll_seconds,
            default_branch=default_branch,
            enable_escalation=enable_escalation,
            enable_stage_md_commits=enable_stage_md_commits,
            api_port=api_port,
            store_backend=store_backend,
            sqlite_file=Path(raw_sqlite_file).resolve(),
            cors_allow_all=cors_allow_all,
            cors_origins=cors_origins,
            docker_preview_enabled=docker_preview_enabled,
            docker_preview_host=docker_preview_host,
            docker_preview_port_start=docker_preview_port_start,
            docker_preview_port_end=docker_preview_port_end,
            docker_preview_container_port=docker_preview_container_port,
            docker_preview_health_path=docker_preview_health_path,
            docker_preview_cors_origins=docker_preview_cors_origins,
        )

        settings.ensure_directories()
        return settings

    def ensure_directories(self) -> None:
        """Create runtime directories that the API and worker both need."""

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_debug_dir.mkdir(parents=True, exist_ok=True)
        self.logs_user_dir.mkdir(parents=True, exist_ok=True)

    @property
    def jobs_file(self) -> Path:
        """Path of the JSON file that stores job records."""

        return self.data_dir / "jobs.json"

    @property
    def queue_file(self) -> Path:
        """Path of the JSON file that stores queued job IDs."""

        return self.data_dir / "queue.json"

    @property
    def logs_dir(self) -> Path:
        """Directory where per-job log files are stored."""

        return self.data_dir / "logs"

    @property
    def logs_debug_dir(self) -> Path:
        """Directory where per-job debug log files are stored."""

        return self.logs_dir / "debug"

    @property
    def logs_user_dir(self) -> Path:
        """Directory where per-job user-friendly log files are stored."""

        return self.logs_dir / "user"

    def repository_workspace_path(
        self,
        repository_full_name: str,
        app_code: str = "default",
    ) -> Path:
        """Return a safe local path for a repository checkout.

        Example:
            "owner/repo" + "mvp-a" -> "<workspace_dir>/mvp-a/owner__repo"
        """

        safe_app = _sanitize_segment(app_code, fallback="default")
        safe_name = repository_full_name.replace("/", "__")
        return self.workspace_dir / safe_app / safe_name



def _sanitize_segment(value: str, fallback: str) -> str:
    """Keep only filesystem-safe characters for one path segment."""

    lowered = (value or "").strip().lower()
    filtered = "".join(ch for ch in lowered if ch.isalnum() or ch in {"-", "_"})
    return filtered or fallback


def _read_int_env(name: str, default: int) -> int:
    """Read an integer environment variable with a fallback default."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer. Current value: {raw_value}") from error

    if parsed < 1:
        raise ValueError(f"{name} must be >= 1. Current value: {parsed}")
    return parsed
