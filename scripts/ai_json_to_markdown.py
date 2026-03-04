#!/usr/bin/env python3
"""Extract markdown response and token usage from AI CLI JSON output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, Tuple


def _to_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _extract_gemini(payload: Dict[str, Any]) -> Tuple[str, int, int, int]:
    response = str(payload.get("response", "")).strip()
    stats = payload.get("stats", {})
    models = stats.get("models", {}) if isinstance(stats, dict) else {}
    model_payload = next(iter(models.values()), {}) if isinstance(models, dict) else {}
    tokens = model_payload.get("tokens", {}) if isinstance(model_payload, dict) else {}

    input_tokens = _to_int(tokens.get("input", 0))
    output_tokens = _to_int(tokens.get("candidates", 0))
    total_tokens = _to_int(tokens.get("total", input_tokens + output_tokens))
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens

    return response, input_tokens, output_tokens, total_tokens


def _extract_claude(payload: Dict[str, Any]) -> Tuple[str, int, int, int]:
    result = str(payload.get("result", "")).strip()
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}

    input_tokens = _to_int(usage.get("input_tokens", 0))
    output_tokens = _to_int(usage.get("output_tokens", 0))
    total_tokens = input_tokens + output_tokens
    return result, input_tokens, output_tokens, total_tokens


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True, choices=["gemini", "claude"])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    raw = sys.stdin.read()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        output_path.write_text(raw.rstrip() + "\n", encoding="utf-8")
        return 0

    if not isinstance(payload, dict):
        output_path.write_text(raw.rstrip() + "\n", encoding="utf-8")
        return 0

    if args.provider == "gemini":
        text, input_tokens, output_tokens, total_tokens = _extract_gemini(payload)
    else:
        text, input_tokens, output_tokens, total_tokens = _extract_claude(payload)

    output_path.write_text((text.rstrip() + "\n") if text else "", encoding="utf-8")

    print("tokens used")
    print(f"{total_tokens:,}")
    print(f"input_tokens: {input_tokens}")
    print(f"output_tokens: {output_tokens}")
    print(f"total_tokens: {total_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
