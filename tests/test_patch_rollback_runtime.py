from __future__ import annotations

import subprocess

from app.patch_rollback_runtime import PatchRollbackRuntime


def test_patch_rollback_runtime_rolls_branch_back(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    runtime = PatchRollbackRuntime(
        utc_now_iso=lambda: "2026-03-13T12:00:00+09:00",
    )
    responses = {
        ("rev-parse", "HEAD"): "bbbbbbbbbbbbbbbb",
        ("status", "--porcelain"): "",
        ("rev-parse", "--verify", "aaaaaaaa"): "aaaaaaaa",
        ("checkout", "-B", "master", "aaaaaaaa"): "",
        ("rev-parse", "HEAD", "after"): "aaaaaaaa",
    }

    calls: list[tuple[str, ...]] = []

    def fake_run_git(repo_root, args):
        calls.append(tuple(args))
        if tuple(args) == ("rev-parse", "HEAD") and len([c for c in calls if c == ("rev-parse", "HEAD")]) > 1:
            return responses[("rev-parse", "HEAD", "after")]
        return responses[tuple(args)]

    monkeypatch.setattr(runtime, "_run_git", fake_run_git)

    payload = runtime.rollback_to_commit(
        repo_root=tmp_path,
        branch="master",
        target_commit="aaaaaaaa",
    )

    assert payload["source_commit_before"] == "bbbbbbbbbbbbbbbb"
    assert payload["target_commit"] == "aaaaaaaa"
    assert payload["resulting_commit"] == "aaaaaaaa"
    assert payload["operations"] == [
        {"action": "verify", "value": "aaaaaaaa"},
        {"action": "checkout_branch", "value": "master"},
    ]


def test_patch_rollback_runtime_rejects_dirty_worktree(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    runtime = PatchRollbackRuntime(
        utc_now_iso=lambda: "2026-03-13T12:00:00+09:00",
    )

    def fake_run_git(repo_root, args):
        if tuple(args) == ("rev-parse", "HEAD"):
            return "bbbbbbbbbbbbbbbb"
        if tuple(args) == ("status", "--porcelain"):
            return " M README.md"
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(runtime, "_run_git", fake_run_git)

    try:
        runtime.rollback_to_commit(
            repo_root=tmp_path,
            branch="master",
            target_commit="aaaaaaaa",
        )
    except RuntimeError as exc:
        assert "로컬 변경 사항" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
