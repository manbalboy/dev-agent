"""Worker startup sweep trace helpers."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Dict

from app.config import AppSettings
from app.models import JobStatus, utc_now_iso
from app.store import JobStore


def audit_running_node_job_mismatches(store: JobStore) -> Dict[str, Any]:
    """Inspect job/node-run mismatches without mutating runtime state."""

    mismatch_counter: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for job in store.list_jobs():
        node_runs = store.list_node_runs(job.job_id)
        running_node_runs = [item for item in node_runs if str(item.status or "").strip() == "running"]
        current_attempt_running = [
            item for item in running_node_runs if int(item.attempt or 0) == int(job.attempt or 0)
        ]
        mismatch_types: list[str] = []

        if job.status != JobStatus.RUNNING.value and running_node_runs:
            mismatch_types.append("non_running_job_has_running_node_runs")
        if job.status == JobStatus.RUNNING.value and not current_attempt_running:
            mismatch_types.append("running_job_missing_current_running_node")
        if job.status == JobStatus.RUNNING.value and any(
            int(item.attempt or 0) != int(job.attempt or 0) for item in running_node_runs
        ):
            mismatch_types.append("running_job_has_stale_running_node_attempt")
        if job.status == JobStatus.RUNNING.value and len(current_attempt_running) > 1:
            mismatch_types.append("running_job_has_multiple_current_running_nodes")

        if not mismatch_types:
            continue

        for mismatch_type in mismatch_types:
            mismatch_counter[mismatch_type] += 1
        if len(samples) < 10:
            samples.append(
                {
                    "job_id": job.job_id,
                    "job_status": str(job.status or "").strip(),
                    "job_stage": str(job.stage or "").strip(),
                    "attempt": int(job.attempt or 0),
                    "mismatch_types": mismatch_types,
                    "running_node_ids": [str(item.node_id or "").strip() for item in running_node_runs],
                    "running_node_attempts": [int(item.attempt or 0) for item in running_node_runs],
                    "current_attempt_running_count": len(current_attempt_running),
                }
            )

    return {
        "generated_at": utc_now_iso(),
        "total_mismatches": int(sum(mismatch_counter.values()) or 0),
        "counts": {name: int(count) for name, count in mismatch_counter.items()},
        "samples": samples,
    }


def read_worker_startup_sweep_trace(settings: AppSettings) -> Dict[str, Any]:
    """Read worker startup sweep trace payload safely."""

    path = worker_startup_sweep_trace_path(settings)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def worker_startup_sweep_trace_path(settings: AppSettings) -> Path:
    """Return the canonical worker startup sweep trace path."""

    return settings.data_dir / "worker_startup_sweep_trace.json"


def append_worker_startup_sweep_trace(
    settings: AppSettings,
    *,
    orphan_running_node_runs_interrupted: int,
    stale_running_jobs_recovered: int,
    orphan_queued_jobs_recovered: int,
    running_node_job_mismatches_detected: int = 0,
    running_node_job_mismatches_remaining: int = 0,
    queue_size_before: int,
    queue_size_after: int,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Append one worker startup sweep event into a global trace artifact."""

    path = worker_startup_sweep_trace_path(settings)
    now = utc_now_iso()
    payload: Dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {}
    events = payload.get("events", []) if isinstance(payload.get("events"), list) else []
    previous_event_count = int(payload.get("event_count", len(events)) or 0)
    event = {
        "generated_at": now,
        "orphan_running_node_runs_interrupted": int(orphan_running_node_runs_interrupted or 0),
        "stale_running_jobs_recovered": int(stale_running_jobs_recovered or 0),
        "orphan_queued_jobs_recovered": int(orphan_queued_jobs_recovered or 0),
        "running_node_job_mismatches_detected": int(running_node_job_mismatches_detected or 0),
        "running_node_job_mismatches_remaining": int(running_node_job_mismatches_remaining or 0),
        "queue_size_before": int(queue_size_before or 0),
        "queue_size_after": int(queue_size_after or 0),
        "details": details or {},
    }
    events.append(event)
    result = {
        "generated_at": payload.get("generated_at") or now,
        "latest_event_at": now,
        "event_count": max(previous_event_count, len(events) - 1) + 1,
        "events": events[-20:],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
