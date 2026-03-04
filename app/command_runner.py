"""Utilities for running shell commands and AI command templates."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time
from typing import Callable, Dict


LogWriter = Callable[[str], None]


@dataclass
class CommandResult:
    """Result of one shell command execution."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class CommandExecutionError(RuntimeError):
    """Raised when an external command fails and needs actionable messaging."""


class CommandTemplateRunner:
    """Execute AI CLI commands from user-configured templates.

    Template values come from a JSON file so operators can adapt to CLI changes
    without touching Python code.
    """

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def has_template(self, template_name: str) -> bool:
        """Return True when a template key exists."""

        templates = self._load_templates()
        return template_name in templates

    def run_template(
        self,
        template_name: str,
        variables: Dict[str, str],
        cwd: Path,
        log_writer: LogWriter,
    ) -> CommandResult:
        """Render and execute a named template.

        Raises:
            CommandExecutionError: If template is missing or command fails.
        """

        templates = self._load_templates()
        if template_name not in templates:
            raise CommandExecutionError(
                f"AI command template '{template_name}' is not configured. "
                f"Please add it to {self.config_path}."
            )

        template = templates[template_name]
        try:
            rendered_command = template.format(**variables)
        except KeyError as error:
            missing = error.args[0]
            raise CommandExecutionError(
                f"Template '{template_name}' requires variable '{missing}', "
                "but that variable was not provided by the worker."
            ) from error

        return run_shell_command(
            command=rendered_command,
            cwd=cwd,
            log_writer=log_writer,
            check=True,
            command_purpose=f"AI template '{template_name}'",
        )

    def _load_templates(self) -> Dict[str, str]:
        """Load command templates from JSON config."""

        if not self.config_path.exists():
            raise CommandExecutionError(
                f"AI command config file not found: {self.config_path}. "
                "Copy config/ai_commands.example.json to your configured path "
                "and customize each command."
            )

        try:
            raw_payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CommandExecutionError(
                f"AI command config is not valid JSON: {self.config_path}."
            ) from error

        if not isinstance(raw_payload, dict):
            raise CommandExecutionError(
                "AI command config must be a JSON object where each key maps "
                "to one shell command template string."
            )

        templates: Dict[str, str] = {}
        for key, value in raw_payload.items():
            if isinstance(value, str) and value.strip():
                templates[str(key)] = value

        if not templates:
            raise CommandExecutionError(
                "AI command config does not contain any usable template strings."
            )

        return templates



def run_shell_command(
    command: str,
    cwd: Path,
    log_writer: LogWriter,
    check: bool = False,
    command_purpose: str = "command",
) -> CommandResult:
    """Execute one shell command through bash.

    We always run through `bash -lc` so operators can use familiar shell syntax
    in command templates.
    """

    start_time = time.monotonic()
    log_writer(f"[RUN] {command}")

    process = subprocess.run(  # noqa: S603,S607 - intentional shell execution.
        ["bash", "-lc", command],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )

    duration_seconds = time.monotonic() - start_time
    result = CommandResult(
        command=command,
        exit_code=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
        duration_seconds=duration_seconds,
    )

    if result.stdout.strip():
        log_writer("[STDOUT]\n" + result.stdout.rstrip())
    if result.stderr.strip():
        log_writer("[STDERR]\n" + result.stderr.rstrip())
    log_writer(
        f"[DONE] exit_code={result.exit_code} elapsed={result.duration_seconds:.2f}s"
    )

    if check and result.exit_code != 0:
        raise CommandExecutionError(_build_actionable_error(result, command_purpose))

    return result



def _build_actionable_error(result: CommandResult, command_purpose: str) -> str:
    """Create a human-friendly error that suggests the next diagnostic step."""

    stderr_preview = (result.stderr.strip() or "(no stderr output)")[:500]
    return (
        f"{command_purpose} failed with exit code {result.exit_code}. "
        f"Next action: run the logged command manually in the same repository "
        "directory and verify CLI login/state. "
        f"stderr preview: {stderr_preview}"
    )
