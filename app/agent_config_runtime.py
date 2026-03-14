"""Agent template config and safety helpers for dashboard routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException

from app.agent_cli_runtime import check_one_cli, infer_cli_model


def read_command_templates(path: Path) -> Dict[str, str]:
    """Read command template JSON file into string dictionary."""

    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"명령 템플릿 파일이 없습니다: {path}",
        )

    try:
        raw_payload = path.read_text(encoding="utf-8")
        loaded = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=500,
            detail=f"명령 템플릿 JSON 파싱 실패: {path}",
        ) from error

    if not isinstance(loaded, dict):
        raise HTTPException(
            status_code=500,
            detail="명령 템플릿 포맷이 올바르지 않습니다. JSON object여야 합니다.",
        )

    templates: Dict[str, str] = {}
    for key, value in loaded.items():
        if isinstance(value, str):
            templates[str(key)] = value
    return templates


def write_command_templates(path: Path, templates: Dict[str, str]) -> None:
    """Persist command templates as pretty JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(templates, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_env_enable_escalation(env_path: Path, fallback: bool) -> bool:
    """Read AGENTHUB_ENABLE_ESCALATION from .env file if available."""

    if not env_path.exists():
        return fallback

    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("AGENTHUB_ENABLE_ESCALATION="):
            continue
        raw_value = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
        return raw_value in {"1", "true", "yes", "on"}
    return fallback


def set_env_value(env_path: Path, key: str, value: str) -> None:
    """Set or append one KEY=value entry in .env while preserving other lines."""

    lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines() if env_path.exists() else []

    prefix = f"{key}="
    replaced = False
    updated = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) and not stripped.startswith(f"#{prefix}"):
            updated.append(f"{key}={value}")
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        updated.append(f"{key}={value}")

    env_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def load_agent_template_config(command_config_path: Path, env_path: Path, *, enable_escalation_fallback: bool) -> Dict[str, Any]:
    """Return editable command templates and escalation toggle state."""

    templates = read_command_templates(command_config_path)
    return {
        "planner": templates.get("planner", ""),
        "coder": templates.get("coder", ""),
        "reviewer": templates.get("reviewer", ""),
        "copilot": templates.get("copilot", ""),
        "escalation": templates.get("escalation", ""),
        "enable_escalation": read_env_enable_escalation(env_path, enable_escalation_fallback),
    }


def update_agent_template_config(
    command_config_path: Path,
    env_path: Path,
    *,
    planner: str,
    coder: str,
    reviewer: str,
    copilot: str,
    escalation: str,
    enable_escalation: bool,
) -> Dict[str, Any]:
    """Update planner/coder/reviewer templates and escalation toggle."""

    current = read_command_templates(command_config_path)
    current["planner"] = planner.strip()
    current["coder"] = coder.strip()
    current["reviewer"] = reviewer.strip()
    current["copilot"] = copilot.strip()
    current["escalation"] = escalation.strip()
    write_command_templates(command_config_path, current)
    set_env_value(env_path, "AGENTHUB_ENABLE_ESCALATION", "true" if enable_escalation else "false")
    return {"saved": True, "enable_escalation": enable_escalation}


def collect_agent_cli_status(command_config_path: Path) -> Dict[str, Any]:
    """Check whether Gemini/Codex CLIs are executable."""

    templates = read_command_templates(command_config_path)
    return {
        "gemini": check_one_cli("gemini", templates),
        "codex": check_one_cli("codex", templates),
        "git": check_one_cli("git", templates),
        "gh": check_one_cli("gh", templates),
    }


def collect_agent_model_status(command_config_path: Path) -> Dict[str, Any]:
    """Return inferred model settings for Gemini/Codex."""

    templates = read_command_templates(command_config_path)
    return {
        "gemini": infer_cli_model("gemini", templates),
        "codex": infer_cli_model("codex", templates),
    }
