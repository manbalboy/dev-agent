from __future__ import annotations

from pathlib import Path

from app.job_log_runtime import JobLogRuntime


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def update_job(self, job_id: str, *, heartbeat_at: str) -> None:
        self.calls.append((job_id, heartbeat_at))


def test_job_log_runtime_append_actor_log_writes_debug_and_user_logs(tmp_path: Path) -> None:
    runtime = JobLogRuntime(
        store=_Store(),
        utc_now_iso=lambda: "2026-03-13T00:00:00Z",
    )
    calls: list[str] = []
    log_path = tmp_path / "job.log"

    runtime.append_actor_log(
        log_path,
        "coder",
        "[DONE] exit_code=1 elapsed=2.0s",
        touch_job_heartbeat=lambda: calls.append("heartbeat"),
    )

    debug_log = (tmp_path / "debug" / "job.log").read_text(encoding="utf-8")
    user_log = (tmp_path / "user" / "job.log").read_text(encoding="utf-8")

    assert "[ACTOR:CODER] [DONE] exit_code=1 elapsed=2.0s" in debug_log
    assert "[ACTOR:CODER] [DONE] exit_code=1 elapsed=2.0s" in user_log
    assert calls == ["heartbeat"]


def test_job_log_runtime_does_not_emit_run_lines_to_user_log(tmp_path: Path) -> None:
    runtime = JobLogRuntime(
        store=_Store(),
        utc_now_iso=lambda: "2026-03-13T00:00:00Z",
    )

    runtime.append_actor_log(
        tmp_path / "job.log",
        "git",
        "[RUN] git status",
        touch_job_heartbeat=lambda: None,
    )

    assert (tmp_path / "debug" / "job.log").exists()
    assert not (tmp_path / "user" / "job.log").exists()


def test_job_log_runtime_touch_job_heartbeat_throttles_by_interval() -> None:
    store = _Store()
    monotonic_values = iter([100.0, 105.0, 121.0])
    runtime = JobLogRuntime(
        store=store,
        utc_now_iso=lambda: "2026-03-13T00:00:00Z",
        monotonic_fn=lambda: next(monotonic_values),
    )

    first = runtime.touch_job_heartbeat(active_job_id="job-1", last_heartbeat_monotonic=0.0, force=False)
    second = runtime.touch_job_heartbeat(active_job_id="job-1", last_heartbeat_monotonic=first, force=False)
    third = runtime.touch_job_heartbeat(active_job_id="job-1", last_heartbeat_monotonic=second, force=False)

    assert first == 100.0
    assert second == 100.0
    assert third == 121.0
    assert store.calls == [
        ("job-1", "2026-03-13T00:00:00Z"),
        ("job-1", "2026-03-13T00:00:00Z"),
    ]


def test_job_log_runtime_infer_actor_from_command() -> None:
    assert JobLogRuntime.infer_actor_from_command("codex exec", "implement") == "CODER"
    assert JobLogRuntime.infer_actor_from_command("gh pr create", "publish") == "GITHUB"
    assert JobLogRuntime.infer_actor_from_command("git push", "publish") == "GIT"
