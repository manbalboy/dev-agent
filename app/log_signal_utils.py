"""Utilities for classifying noisy log/auth signals in operator surfaces."""

from __future__ import annotations

import re

OPTIONAL_HELPER_ACTORS = {
    "COMMIT_SUMMARY",
    "TECH_WRITER",
    "TECH_WRITER_CODEX",
    "PR_SUMMARY",
    "ESCALATION",
    "CODEX_HELPER",
    "COPILOT",
}


def normalize_log_actor(actor: str | None) -> str:
    """Normalize actor names for log classification."""

    return str(actor or "").strip().upper()


def is_optional_helper_actor(actor: str | None) -> bool:
    """Return whether one log actor is an optional helper stage."""

    return normalize_log_actor(actor) in OPTIONAL_HELPER_ACTORS


def classify_cli_health_hint(text: str | None, *, actor: str | None = None) -> str:
    """Return a short Korean operator hint for auth/quota/login-related failures."""

    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    actor_key = normalize_log_actor(actor)

    provider_label = "CLI"
    if "gh " in lowered or "github" in lowered or actor_key == "GITHUB":
        provider_label = "GitHub CLI"
    elif "gemini" in lowered or actor_key in {"PLANNER", "REVIEWER", "TESTER_GEMINI", "COMMIT_SUMMARY", "PR_SUMMARY", "ESCALATION", "TEST_REVIEWER", "SUMMARY_REVIEWER"}:
        provider_label = "Gemini CLI"
    elif "codex" in lowered or actor_key in {"CODER", "CODEX_HELPER", "COPILOT", "TECH_WRITER", "TECH_WRITER_CODEX"}:
        provider_label = "Codex CLI"

    if any(token in lowered for token in ("quota", "no quota", "rate limit", "usage limit")):
        return f"{provider_label} 사용량/쿼터 확인 필요"
    if any(
        token in lowered
        for token in (
            "verify cli login/state",
            "login/state",
            "auth required",
            "authentication",
            "not logged",
            "login required",
            "please login",
            "unauthorized",
            "forbidden",
            "credentials",
            "token expired",
        )
    ):
        return f"{provider_label} 로그인/인증 상태 확인 필요"
    return ""


def summarize_optional_route_error(error: object, *, actor: str | None = None) -> str:
    """Compress noisy helper-route errors into short operator-facing hints."""

    raw = str(error or "").strip()
    if not raw:
        return "보조 CLI 실행 실패"

    auth_hint = classify_cli_health_hint(raw, actor=actor)
    if auth_hint:
        return auth_hint

    exit_match = re.search(r"exit code\s+(\d+)", raw, flags=re.IGNORECASE)
    if exit_match:
        return f"CLI 실행 실패(exit {exit_match.group(1)})"

    first_sentence = raw.split(". Next action:", 1)[0].strip()
    if first_sentence:
        return first_sentence[:160]
    return raw[:160]
