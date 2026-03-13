"""Common artifact I/O helpers extracted from orchestrator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional


class ArtifactIoRuntime:
    """Stateless helpers for JSON/text artifact persistence and parsing."""

    @staticmethod
    def upsert_jsonl_entries(path: Path, entries: List[Dict[str, Any]], *, key_field: str) -> None:
        """Upsert deterministic records into a JSONL file while keeping append-only shape."""

        existing: List[Dict[str, Any]] = []
        if path.exists():
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    existing.append(payload)

        merged: Dict[str, Dict[str, Any]] = {}
        ordered_keys: List[str] = []
        for item in existing + list(entries):
            item_id = str(item.get(key_field, "")).strip()
            if not item_id:
                continue
            if item_id not in merged:
                ordered_keys.append(item_id)
            merged[item_id] = item

        lines = [json.dumps(merged[item_id], ensure_ascii=False) for item_id in ordered_keys]
        path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

    @staticmethod
    def upsert_json_history_entries(
        path: Path,
        entries: List[Dict[str, Any]],
        *,
        key_field: str,
        root_key: str,
        max_entries: int,
    ) -> None:
        """Upsert deterministic history entries into one JSON document."""

        payload: Dict[str, Any] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(raw, dict):
                    payload = raw
            except json.JSONDecodeError:
                payload = {}

        current_entries = payload.get(root_key, []) if isinstance(payload, dict) else []
        if not isinstance(current_entries, list):
            current_entries = []
        merged: Dict[str, Dict[str, Any]] = {}
        ordered_keys: List[str] = []
        for item in current_entries + list(entries):
            item_id = str(item.get(key_field, "")).strip()
            if not item_id:
                continue
            if item_id not in merged:
                ordered_keys.append(item_id)
            merged[item_id] = item
        if max_entries > 0 and len(ordered_keys) > max_entries:
            ordered_keys = ordered_keys[-max_entries:]
        payload[root_key] = [merged[item_id] for item_id in ordered_keys]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def write_json_artifact(path: Optional[Path], payload: Dict[str, Any]) -> None:
        """Persist one JSON artifact if path exists."""

        if path is None:
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def read_json_file(path: Optional[Path]) -> Dict[str, Any]:
        """Read JSON file safely and return object fallback."""

        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def read_text_file(path: Optional[Path]) -> str:
        """Read one text file safely."""

        if path is None or not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def extract_review_todo_items(review_text: str) -> List[str]:
        """Extract actionable TODO lines from REVIEW.md."""

        items: List[str] = []
        for raw in str(review_text or "").splitlines():
            line = raw.strip()
            match = re.match(r"^[-*]\s*\[\s?\]\s*(.+)$", line)
            if match:
                todo = match.group(1).strip()
                if todo:
                    items.append(todo)
        return items

    @staticmethod
    def stable_issue_id(raw_text: str) -> str:
        """Generate deterministic issue id from text."""

        normalized = re.sub(r"\s+", " ", str(raw_text or "").strip().lower())
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
        return f"issue_{digest}"
