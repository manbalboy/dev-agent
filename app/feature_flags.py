"""Runtime feature flag helpers for gradual adaptive rollout.

Feature flags are stored in a JSON file so operators can enable/disable
adaptive capabilities without changing code.
"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Dict


DEFAULT_FEATURE_FLAGS: Dict[str, bool] = {
    "memory_logging": True,
    "memory_retrieval": True,
    "convention_extraction": True,
    "memory_scoring": True,
    "strategy_shadow": True,
}

FEATURE_FLAG_LABELS: Dict[str, str] = {
    "memory_logging": "Structured Memory Logging",
    "memory_retrieval": "Controlled Retrieval",
    "convention_extraction": "Convention Extraction",
    "memory_scoring": "Memory Quality Scoring",
    "strategy_shadow": "Adaptive Strategy Shadow",
}

FEATURE_FLAG_DESCRIPTIONS: Dict[str, str] = {
    "memory_logging": "review/improvement 결과를 structured memory artifact로 기록합니다.",
    "memory_retrieval": "planner/reviewer/coder 전에 memory selection/context를 생성해 read-only로 주입합니다.",
    "convention_extraction": "repo 구조/매니페스트/테스트 패턴에서 conventions를 추출합니다.",
    "memory_scoring": "memory feedback/rankings를 계산해 promote/decay/banned 상태를 갱신합니다.",
    "strategy_shadow": "실제 전략은 유지한 채 memory-aware shadow strategy를 비교 기록합니다.",
}


def normalize_feature_flags(raw: Dict[str, Any] | None) -> Dict[str, bool]:
    """Return normalized flags merged with defaults."""

    flags = dict(DEFAULT_FEATURE_FLAGS)
    if not isinstance(raw, dict):
        return flags
    for key in DEFAULT_FEATURE_FLAGS:
        if key in raw:
            flags[key] = bool(raw.get(key))
    return flags


def read_feature_flags(path: Path) -> Dict[str, bool]:
    """Read feature flags from JSON file; fallback to defaults on any error."""

    if not path.exists():
        return dict(DEFAULT_FEATURE_FLAGS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_FEATURE_FLAGS)
    if isinstance(payload, dict) and isinstance(payload.get("flags"), dict):
        return normalize_feature_flags(payload.get("flags"))
    return normalize_feature_flags(payload if isinstance(payload, dict) else None)


def write_feature_flags(path: Path, flags: Dict[str, Any]) -> Dict[str, bool]:
    """Persist normalized feature flags and return saved values."""

    normalized = normalize_feature_flags(flags)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "flags": normalized,
        "labels": FEATURE_FLAG_LABELS,
        "descriptions": FEATURE_FLAG_DESCRIPTIONS,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def feature_flags_payload(path: Path) -> Dict[str, Any]:
    """Return flags plus labels/descriptions for settings UI."""

    return {
        "flags": read_feature_flags(path),
        "defaults": dict(DEFAULT_FEATURE_FLAGS),
        "labels": dict(FEATURE_FLAG_LABELS),
        "descriptions": dict(FEATURE_FLAG_DESCRIPTIONS),
    }


def is_feature_enabled(path: Path, flag_name: str) -> bool:
    """Convenience boolean lookup with default fallback."""

    return bool(read_feature_flags(path).get(flag_name, DEFAULT_FEATURE_FLAGS.get(flag_name, False)))
