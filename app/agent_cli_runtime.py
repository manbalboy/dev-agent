"""CLI inspection helpers for dashboard agent/runtime management."""

from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from fastapi import HTTPException


ASSISTANT_PROVIDER_ALIASES = {"claude": "codex", "copilot": "codex"}


def canonical_cli_name(cli_name: str) -> str:
    """Map legacy provider aliases to the active runtime provider."""

    normalized = str(cli_name or "").strip().lower()
    return ASSISTANT_PROVIDER_ALIASES.get(normalized, normalized)


def check_one_cli(cli_name: str, templates: Dict[str, str]) -> Dict[str, Any]:
    """Probe one CLI using template-derived paths then PATH fallback."""

    candidates = _build_cli_probe_candidates(cli_name, templates)
    for args in candidates:
        probe = _run_probe(args)
        if probe["ok"]:
            return {
                "ok": True,
                "command": " ".join(args),
                "output": probe["output"],
            }

    last = _run_probe(candidates[-1])
    return {
        "ok": False,
        "command": " ".join(candidates[-1]),
        "output": last["output"],
    }


def resolve_codex_command_prefix(templates: Dict[str, str]) -> List[str]:
    """Resolve executable prefix for Codex command under systemd/non-login shells."""

    candidates: List[List[str]] = []
    env_codex = os.getenv("AGENTHUB_CODEX_BIN", "").strip()
    if env_codex:
        candidates.append([env_codex])

    template_text = " ".join(templates.values())
    absolute_paths = re.findall(r"(/[^ \t\"']+)", template_text)
    node_paths = [path for path in absolute_paths if path.endswith("/node")]
    codex_paths = [
        path for path in absolute_paths if path.endswith("/codex") or path.endswith("/codex.js")
    ]
    for path in codex_paths:
        if path.endswith(".js") and node_paths:
            candidates.append([node_paths[0], path])
        candidates.append([path])

    for known in [
        "/root/.nvm/versions/node/v24.14.0/bin/codex",
        "/usr/local/bin/codex",
        "/usr/bin/codex",
    ]:
        candidates.append([known])

    which_codex = shutil.which("codex")
    if which_codex:
        candidates.append([which_codex])

    deduped = _dedupe_command_candidates(candidates)
    for prefix in deduped:
        try:
            probe = subprocess.run(
                [*prefix, "--version"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return prefix

    tried = ", ".join(" ".join(item) for item in deduped) or "(none)"
    raise HTTPException(
        status_code=500,
        detail=(
            "Codex 실행 파일을 찾지 못했습니다. "
            "환경변수 `AGENTHUB_CODEX_BIN`에 Codex 절대경로를 설정해주세요. "
            f"탐색 경로: {tried}"
        ),
    )


def resolve_cli_command_prefix(
    cli_name: str,
    templates: Dict[str, str],
    *,
    env_var: str = "",
) -> List[str]:
    """Resolve executable prefix for generic CLI commands."""

    cli_name = canonical_cli_name(cli_name)
    candidates: List[List[str]] = []
    if env_var:
        env_path = os.getenv(env_var, "").strip()
        if env_path:
            candidates.append([env_path])

    template_text = " ".join(templates.values())
    absolute_paths = re.findall(r"(/[^ \t\"']+)", template_text)
    node_paths = [path for path in absolute_paths if path.endswith("/node")]
    cli_paths = [
        path for path in absolute_paths if path.endswith(f"/{cli_name}") or path.endswith(f"/{cli_name}.js")
    ]
    for path in cli_paths:
        if path.endswith(".js") and node_paths:
            candidates.append([node_paths[0], path])
        candidates.append([path])

    which_cli = shutil.which(cli_name)
    if which_cli:
        candidates.append([which_cli])
    candidates.append([cli_name])

    deduped = _dedupe_command_candidates(candidates)
    for prefix in deduped:
        try:
            probe = subprocess.run(
                [*prefix, "--version"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return prefix

    tried = ", ".join(" ".join(item) for item in deduped) or "(none)"
    raise HTTPException(
        status_code=500,
        detail=f"{cli_name} 실행 파일을 찾지 못했습니다. 탐색 경로: {tried}",
    )


def infer_cli_model(cli_name: str, templates: Dict[str, str]) -> Dict[str, Any]:
    """Infer model name from command templates first, then environment."""

    cli_name = canonical_cli_name(cli_name)
    danger_template_keys = _detect_cli_danger_template_keys(cli_name, templates)
    from_template = _infer_model_from_templates(cli_name, templates)
    if from_template is not None:
        return {
            "model": from_template["model"],
            "source": from_template["source"],
            "template_key": from_template["template_key"],
            "danger_mode": bool(danger_template_keys),
            "danger_template_keys": danger_template_keys,
        }

    from_env = _infer_model_from_env(cli_name)
    if from_env is not None:
        return {
            "model": from_env["model"],
            "source": from_env["source"],
            "template_key": "",
            "danger_mode": bool(danger_template_keys),
            "danger_template_keys": danger_template_keys,
        }

    from_runtime = _infer_model_from_runtime_files(cli_name)
    if from_runtime is not None:
        return {
            "model": from_runtime["model"],
            "source": from_runtime["source"],
            "template_key": "",
            "danger_mode": bool(danger_template_keys),
            "danger_template_keys": danger_template_keys,
        }

    return {
        "model": "",
        "source": "not_found",
        "template_key": "",
        "danger_mode": bool(danger_template_keys),
        "danger_template_keys": danger_template_keys,
    }


def _build_cli_probe_candidates(cli_name: str, templates: Dict[str, str]) -> List[List[str]]:
    """Build probe command candidates from known paths and templates."""

    cli_name = canonical_cli_name(cli_name)
    known: List[List[str]] = []
    template_text = " ".join(templates.values())
    absolute_paths = re.findall(r"(/[^ \t\"']+)", template_text)
    node_paths = [path for path in absolute_paths if path.endswith("/node")]
    cli_paths = [
        path
        for path in absolute_paths
        if path.endswith(f"/{cli_name}") or path.endswith(f"/{cli_name}.js")
    ]

    for path in cli_paths:
        if node_paths and path.startswith("/"):
            known.append([node_paths[0], path, "--version"])
        known.append([path, "--version"])

    known.append([cli_name, "--version"])
    return _dedupe_command_candidates(known)


def _dedupe_command_candidates(candidates: List[List[str]]) -> List[List[str]]:
    """Return command candidates without duplicates while preserving order."""

    deduped: List[List[str]] = []
    seen: set[str] = set()
    for args in candidates:
        key = " ".join(args)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(args)
    return deduped


def _run_probe(args: List[str]) -> Dict[str, Any]:
    """Run one probe command and capture compact output."""

    try:
        process = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "timeout"}
    except OSError as error:
        return {"ok": False, "output": str(error)}

    output = (process.stdout or process.stderr or "").strip().splitlines()
    first_line = output[0] if output else ""
    return {"ok": process.returncode == 0, "output": first_line[:240]}


def _infer_model_from_templates(cli_name: str, templates: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Find explicit --model/-m style option in matching template command."""

    cli_name = canonical_cli_name(cli_name)
    for key, command in templates.items():
        lowered = str(command or "").lower()
        if cli_name not in lowered:
            continue

        match = re.search(r"(?:--model|-m)\s+([^\s\"']+)", command)
        if match:
            return {
                "model": match.group(1),
                "source": "template_flag",
                "template_key": key,
            }

        match = re.search(r"(?:model|MODEL)=([^\s\"']+)", command)
        if match:
            return {
                "model": match.group(1),
                "source": "template_assignment",
                "template_key": key,
            }
    return None


def _detect_cli_danger_template_keys(cli_name: str, templates: Dict[str, str]) -> List[str]:
    """Return template keys that use dangerous CLI bypass flags for one provider."""

    cli_name = canonical_cli_name(cli_name)
    matches: List[str] = []
    for key, command in templates.items():
        lowered = str(command or "").lower()
        if cli_name not in lowered:
            continue
        if "--dangerously-bypass-approvals-and-sandbox" not in lowered:
            continue
        matches.append(str(key))
    return matches


def _infer_model_from_env(cli_name: str) -> Optional[Dict[str, str]]:
    """Infer model from common environment variable names."""

    cli_name = canonical_cli_name(cli_name)
    candidates: Dict[str, List[str]] = {
        "gemini": ["GEMINI_MODEL", "AGENTHUB_GEMINI_MODEL"],
        "codex": ["CODEX_MODEL", "OPENAI_MODEL", "AGENTHUB_CODEX_MODEL"],
    }
    for env_name in candidates.get(cli_name, []):
        value = os.getenv(env_name, "").strip()
        if value:
            return {"model": value, "source": f"env:{env_name}"}
    return None


def _infer_model_from_runtime_files(cli_name: str) -> Optional[Dict[str, str]]:
    """Infer model from the latest local runtime/session files."""

    cli_name = canonical_cli_name(cli_name)
    if cli_name == "gemini":
        candidates = _recent_files(Path("/root/.gemini"), "tmp/**/chats/*.json")
        model = _find_model_in_recent_files(candidates, [r'"model"\s*:\s*"([^"]+)"'])
        if model:
            return {"model": model, "source": "runtime:gemini_chats"}
        return None

    if cli_name == "codex":
        files: List[Path] = []
        files.extend(_recent_files(Path("/root/.codex"), "history.jsonl", limit=1))
        files.extend(_recent_files(Path("/root/.codex"), "sessions/**/*.jsonl"))
        model = _find_model_in_recent_files(
            files,
            [
                r'"model"\s*:\s*"([^"]+)"',
                r'"model_slug"\s*:\s*"([^"]+)"',
                r'"model_name"\s*:\s*"([^"]+)"',
            ],
        )
        if model:
            return {"model": model, "source": "runtime:codex_sessions"}
        return None

    return None


def _recent_files(base: Path, pattern: str, limit: int = 20) -> List[Path]:
    """Return recent files matching glob pattern, newest first."""

    if not base.exists():
        return []
    matched = [path for path in base.glob(pattern) if path.is_file()]
    matched.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return matched[:limit]


def _find_model_in_recent_files(files: List[Path], regexes: List[str]) -> Optional[str]:
    """Search recent files for model-like fields and return the first match."""

    compiled = [re.compile(pattern) for pattern in regexes]
    for file_path in files:
        text = _read_file_tail(file_path, max_bytes=250_000)
        for regex in compiled:
            matches = regex.findall(text)
            if not matches:
                continue
            candidate = str(matches[-1]).strip()
            if candidate:
                return candidate
    return None


def _read_file_tail(path: Path, max_bytes: int) -> str:
    """Read at most `max_bytes` from the end of file as text."""

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            raw = handle.read()
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""
