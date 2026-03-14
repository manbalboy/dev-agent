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
    worker_stale_running_seconds: int
    worker_max_auto_recoveries: int
    default_branch: str
    enable_escalation: bool
    enable_stage_md_commits: bool
    api_port: int
    store_backend: str
    sqlite_file: Path
    patch_updater_poll_seconds: int = 5
    patch_api_service_name: str = "agenthub-api"
    patch_worker_service_name: str = "agenthub-worker"
    patch_updater_service_name: str = "agenthub-updater"
    durable_retention_days: int = 7
    self_check_stale_minutes: int = 45
    self_check_alert_webhook_url: str = ""
    self_check_alert_critical_webhook_url: str = ""
    self_check_alert_webhook_timeout_seconds: int = 10
    self_check_alert_repeat_minutes: int = 180
    self_check_alert_failure_backoff_max_minutes: int = 720
    public_base_url: str = ""
    enforce_https: bool = False
    trust_x_forwarded_proto: bool = False
    memory_enabled: bool = False
    memory_dir: Path = Path("memory")
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
        worker_stale_running_seconds = _read_int_env(
            "AGENTHUB_WORKER_STALE_RUNNING_SECONDS",
            default=1800,
        )
        worker_max_auto_recoveries = _read_int_env(
            "AGENTHUB_WORKER_MAX_AUTO_RECOVERIES",
            default=2,
        )
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
        patch_updater_poll_seconds = _read_int_env(
            "AGENTHUB_PATCH_UPDATER_POLL_SECONDS",
            default=5,
        )
        patch_api_service_name = os.getenv("AGENTHUB_PATCH_API_SERVICE_NAME", "agenthub-api").strip() or "agenthub-api"
        patch_worker_service_name = os.getenv("AGENTHUB_PATCH_WORKER_SERVICE_NAME", "agenthub-worker").strip() or "agenthub-worker"
        patch_updater_service_name = os.getenv("AGENTHUB_PATCH_UPDATER_SERVICE_NAME", "agenthub-updater").strip() or "agenthub-updater"
        durable_retention_days = _read_int_env("AGENTHUB_DURABLE_RETENTION_DAYS", default=7)
        self_check_stale_minutes = _read_int_env("AGENTHUB_SELF_CHECK_STALE_MINUTES", default=45)
        self_check_alert_webhook_url = os.getenv("AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_URL", "").strip()
        self_check_alert_critical_webhook_url = os.getenv(
            "AGENTHUB_SELF_CHECK_ALERT_CRITICAL_WEBHOOK_URL",
            "",
        ).strip()
        self_check_alert_webhook_timeout_seconds = _read_int_env(
            "AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_TIMEOUT_SECONDS",
            default=10,
        )
        self_check_alert_repeat_minutes = _read_int_env(
            "AGENTHUB_SELF_CHECK_ALERT_REPEAT_MINUTES",
            default=180,
        )
        self_check_alert_failure_backoff_max_minutes = _read_int_env(
            "AGENTHUB_SELF_CHECK_ALERT_FAILURE_BACKOFF_MAX_MINUTES",
            default=720,
        )
        public_base_url = os.getenv("AGENTHUB_PUBLIC_BASE_URL", "").strip().rstrip("/")
        enforce_https = (
            os.getenv("AGENTHUB_ENFORCE_HTTPS", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        trust_x_forwarded_proto = (
            os.getenv("AGENTHUB_TRUST_X_FORWARDED_PROTO", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        cors_allow_all = (
            os.getenv("AGENTHUB_CORS_ALLOW_ALL", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        cors_origins = os.getenv("AGENTHUB_CORS_ORIGINS", "*").strip() or "*"
        memory_enabled = (
            os.getenv("MEMORY_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        raw_memory_dir = os.getenv("MEMORY_DIR", str(Path(raw_data_dir) / "memory"))

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
            worker_stale_running_seconds=worker_stale_running_seconds,
            worker_max_auto_recoveries=worker_max_auto_recoveries,
            default_branch=default_branch,
            enable_escalation=enable_escalation,
            enable_stage_md_commits=enable_stage_md_commits,
            api_port=api_port,
            store_backend=store_backend,
            sqlite_file=Path(raw_sqlite_file).resolve(),
            patch_updater_poll_seconds=patch_updater_poll_seconds,
            patch_api_service_name=patch_api_service_name,
            patch_worker_service_name=patch_worker_service_name,
            patch_updater_service_name=patch_updater_service_name,
            durable_retention_days=durable_retention_days,
            self_check_stale_minutes=self_check_stale_minutes,
            self_check_alert_webhook_url=self_check_alert_webhook_url,
            self_check_alert_critical_webhook_url=self_check_alert_critical_webhook_url,
            self_check_alert_webhook_timeout_seconds=self_check_alert_webhook_timeout_seconds,
            self_check_alert_repeat_minutes=self_check_alert_repeat_minutes,
            self_check_alert_failure_backoff_max_minutes=self_check_alert_failure_backoff_max_minutes,
            public_base_url=public_base_url,
            enforce_https=enforce_https,
            trust_x_forwarded_proto=trust_x_forwarded_proto,
            cors_allow_all=cors_allow_all,
            cors_origins=cors_origins,
            memory_enabled=memory_enabled,
            memory_dir=Path(raw_memory_dir).resolve(),
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
        if self.memory_enabled:
            self.resolved_memory_dir.mkdir(parents=True, exist_ok=True)

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

    @property
    def patch_updater_status_file(self) -> Path:
        """Path where the standalone updater service writes heartbeat/status."""

        return self.data_dir / "patch_updater_status.json"

    @property
    def patch_lock_file(self) -> Path:
        """Path where patch drain/restart temporarily blocks new job intake."""

        return self.data_dir / "patch_operation_lock.json"

    @property
    def patch_backups_dir(self) -> Path:
        """Directory where patch updater stores pre-patch backup snapshots."""

        return self.data_dir / "patch_backups"

    @property
    def durable_runtime_hygiene_report_file(self) -> Path:
        """Path where durable runtime cleanup writes its latest audit payload."""

        return self.data_dir / "durable_runtime_hygiene_report.json"

    @property
    def durable_runtime_self_check_report_file(self) -> Path:
        """Path where periodic durable runtime self-check writes its latest report."""

        return self.data_dir / "durable_runtime_self_check_report.json"

    @property
    def durable_runtime_self_check_alert_file(self) -> Path:
        """Path where periodic durable runtime self-check writes its latest alert state."""

        return self.data_dir / "durable_runtime_self_check_alert.json"

    @property
    def durable_runtime_self_check_alert_delivery_file(self) -> Path:
        """Path where periodic durable runtime self-check stores alert delivery state."""

        return self.data_dir / "durable_runtime_self_check_alert_delivery.json"

    @property
    def resolved_memory_dir(self) -> Path:
        """Return one stable memory directory path for both env and tests."""

        if self.memory_dir.is_absolute():
            return self.memory_dir
        return (self.data_dir / self.memory_dir).resolve()

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
