"""Docker preview/runtime helpers for orchestrator."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
import socket
import time
from typing import Dict, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from app.models import JobRecord


class PreviewRuntime:
    """Encapsulate preview deploy, probe, and PR section helpers."""

    def __init__(
        self,
        *,
        settings,
        run_shell,
        execute_shell_command,
        actor_log_writer,
        append_actor_log,
        docs_file,
    ) -> None:
        self.settings = settings
        self.run_shell = run_shell
        self.execute_shell_command = execute_shell_command
        self.actor_log_writer = actor_log_writer
        self.append_actor_log = append_actor_log
        self.docs_file = docs_file

    def deploy_preview_and_smoke_test(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ) -> Dict[str, str]:
        """Build/run Docker preview and return metadata for PR body."""

        info: Dict[str, str] = {
            "status": "skipped",
            "reason": "",
            "container_name": "",
            "image_tag": "",
            "port": "",
            "external_url": "",
            "local_url": "",
            "health_url": "",
            "cors_origins": self.settings.docker_preview_cors_origins,
        }

        if not self.settings.docker_preview_enabled:
            info["reason"] = "Docker preview is disabled by configuration."
            self.write_preview_markdown(repository_path, info)
            return info

        dockerfile_path = repository_path / "Dockerfile"
        if not dockerfile_path.exists():
            info["reason"] = "Dockerfile not found in repository root."
            self.append_actor_log(log_path, "DOCKER", info["reason"])
            self.write_preview_markdown(repository_path, info)
            return info

        port = self.allocate_preview_port()
        if port is None:
            info["reason"] = (
                f"No available preview port in range "
                f"{self.settings.docker_preview_port_start}-{self.settings.docker_preview_port_end}."
            )
            self.append_actor_log(log_path, "DOCKER", info["reason"])
            self.write_preview_markdown(repository_path, info)
            return info

        container_name = f"agenthub-preview-{job.job_id[:8]}"
        image_tag = f"agenthub/{job.app_code}-{job.job_id[:8]}:latest"
        container_port = self.detect_container_port(repository_path)
        external_url = f"http://{self.settings.docker_preview_host}:{port}"
        local_url = f"http://127.0.0.1:{port}"
        health_url = f"{local_url}{self.settings.docker_preview_health_path}"

        info.update(
            {
                "container_name": container_name,
                "image_tag": image_tag,
                "port": str(port),
                "container_port": str(container_port),
                "external_url": external_url,
                "local_url": local_url,
                "health_url": health_url,
            }
        )

        try:
            self.run_shell(
                command="docker --version",
                cwd=repository_path,
                log_path=log_path,
                purpose="check docker cli",
            )

            self.execute_shell_command(
                command=f"docker rm -f {shlex.quote(container_name)}",
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "DOCKER"),
                check=False,
                command_purpose="cleanup previous preview container",
            )

            self.run_shell(
                command=f"docker build -t {shlex.quote(image_tag)} .",
                cwd=repository_path,
                log_path=log_path,
                purpose="docker build preview image",
            )
            self.run_shell(
                command=(
                    f"docker run -d --name {shlex.quote(container_name)} "
                    f"-p {port}:{container_port} "
                    f"-e PORT={container_port} "
                    f"-e CORS_ALLOWED_ORIGINS={shlex.quote(self.settings.docker_preview_cors_origins)} "
                    f"{shlex.quote(image_tag)}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="docker run preview container",
            )

            is_healthy = False
            for _ in range(20):
                if self.probe_http(health_url):
                    is_healthy = True
                    break
                time.sleep(1)

            if is_healthy:
                info["status"] = "running"
                info["reason"] = "Preview container is reachable."
                self.append_actor_log(
                    log_path,
                    "DOCKER",
                    f"Preview running at {external_url} (health: {health_url})",
                )
            else:
                info["status"] = "failed"
                info["reason"] = "Container started but health check did not pass in time."
                self.append_actor_log(log_path, "DOCKER", info["reason"])
        except Exception as error:  # noqa: BLE001
            info["status"] = "failed"
            info["reason"] = f"Docker preview failed: {error}"
            self.append_actor_log(log_path, "DOCKER", info["reason"])

        self.write_preview_markdown(repository_path, info)
        return info

    def detect_container_port(self, repository_path: Path) -> int:
        """Detect container port from Dockerfile EXPOSE, fallback to configured default."""

        dockerfile = repository_path / "Dockerfile"
        default_port = int(self.settings.docker_preview_container_port)
        if not dockerfile.exists():
            return default_port
        try:
            content = dockerfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return default_port

        match = re.search(r"^\s*EXPOSE\s+(\d+)", content, flags=re.IGNORECASE | re.MULTILINE)
        if not match:
            return default_port
        try:
            parsed = int(match.group(1))
        except ValueError:
            return default_port
        if parsed < 1 or parsed > 65535:
            return default_port
        return parsed

    def append_preview_section_to_pr_body(self, pr_body_path: Path, preview_info: Dict[str, str]) -> None:
        """Append deployment preview metadata so PR always includes pod/container info."""

        current = ""
        if pr_body_path.exists():
            current = pr_body_path.read_text(encoding="utf-8", errors="replace").rstrip() + "\n\n"

        section = self.build_preview_pr_section(preview_info)
        pr_body_path.write_text(current + section, encoding="utf-8")

    @staticmethod
    def build_preview_pr_section(preview_info: Dict[str, str]) -> str:
        """Render markdown section for Docker preview status."""

        status = preview_info.get("status", "skipped")
        reason = preview_info.get("reason", "")
        container_name = preview_info.get("container_name", "")
        port = preview_info.get("port", "")
        container_port = preview_info.get("container_port", "")
        external_url = preview_info.get("external_url", "")
        health_url = preview_info.get("health_url", "")
        cors_origins = preview_info.get("cors_origins", "")

        lines = [
            "## Deployment Preview",
            f"- Docker Pod/Container: `{container_name or 'n/a'}`",
            f"- Status: `{status}`",
        ]
        if port:
            lines.append(f"- External port: `{port}` (7000 range policy)")
        if container_port:
            lines.append(f"- Container port: `{container_port}`")
        if external_url:
            lines.append(f"- External URL: {external_url}")
        if health_url:
            lines.append(f"- Health probe: {health_url}")
        if cors_origins:
            lines.append(f"- CORS allow list: `{cors_origins}`")
        if reason:
            lines.append(f"- Note: {reason}")
        lines.append("")
        return "\n".join(lines)

    def write_preview_markdown(self, repository_path: Path, preview_info: Dict[str, str]) -> None:
        """Persist preview metadata inside workspace for audit/debug."""

        path = self.docs_file(repository_path, "PREVIEW.md")
        lines = [
            "# PREVIEW",
            "",
            f"- Status: `{preview_info.get('status', 'unknown')}`",
            f"- Docker Pod/Container: `{preview_info.get('container_name', 'n/a')}`",
            f"- Image: `{preview_info.get('image_tag', 'n/a')}`",
            f"- Container Port: `{preview_info.get('container_port', 'n/a')}`",
            f"- External URL: {preview_info.get('external_url', 'n/a')}",
            f"- Health URL: {preview_info.get('health_url', 'n/a')}",
            f"- CORS: `{preview_info.get('cors_origins', '')}`",
            f"- Note: {preview_info.get('reason', '')}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")

    def allocate_preview_port(self) -> Optional[int]:
        """Allocate one free host port in configured preview range."""

        for port in range(self.settings.docker_preview_port_start, self.settings.docker_preview_port_end + 1):
            if self.is_local_port_in_use(port):
                continue
            return port
        return None

    @staticmethod
    def is_local_port_in_use(port: int) -> bool:
        """Check localhost TCP port usage."""

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def probe_http(url: str) -> bool:
        """Return True when preview endpoint returns a non-5xx response."""

        req = urlrequest.Request(url, method="GET")
        try:
            with urlrequest.urlopen(req, timeout=2) as resp:
                code = int(getattr(resp, "status", 0))
                return 200 <= code < 500
        except urlerror.URLError:
            return False
