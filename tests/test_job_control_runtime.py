from __future__ import annotations

from pathlib import Path

from app.job_control_runtime import JobControlRuntime


class _Store:
    def __init__(self, job=None) -> None:
        self._job = job

    def get_job(self, job_id: str):
        return self._job


def test_job_control_runtime_stop_signal_round_trip(tmp_path):
    runtime = JobControlRuntime(store=_Store(), data_dir=tmp_path)
    path = runtime.stop_signal_path("job-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stop", encoding="utf-8")

    assert runtime.is_stop_requested("job-1") is True

    runtime.clear_stop_requested("job-1")

    assert runtime.is_stop_requested("job-1") is False


def test_job_control_runtime_normalize_agent_profile():
    assert JobControlRuntime.normalize_agent_profile("") == "primary"
    assert JobControlRuntime.normalize_agent_profile(" fallback ") == "fallback"


def test_job_control_runtime_require_job_raises_for_missing():
    runtime = JobControlRuntime(store=_Store(job=None), data_dir=Path("."))

    try:
        runtime.require_job("missing")
    except KeyError as exc:
        assert "Job not found: missing" in str(exc)
    else:
        raise AssertionError("KeyError expected for missing job")
