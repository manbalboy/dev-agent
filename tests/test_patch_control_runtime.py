from __future__ import annotations

import subprocess

from app.patch_control_runtime import PatchControlRuntime


def test_patch_control_runtime_reports_update_available_with_upstream(monkeypatch, tmp_path):
    runtime = PatchControlRuntime(repo_root=tmp_path, utc_now_iso=lambda: "2026-03-13T10:00:00+09:00")
    (tmp_path / ".git").mkdir()

    responses = {
        ("rev-parse", "--abbrev-ref", "HEAD"): "master",
        ("rev-parse", "HEAD"): "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ("log", "--format=%s", "-n", "1", "HEAD"): "local commit",
        ("status", "--porcelain"): "",
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): "origin/master",
        ("fetch", "--quiet", "origin"): "",
        ("rev-parse", "origin/master"): "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ("log", "--format=%s", "-n", "1", "origin/master"): "remote commit",
        ("rev-list", "--count", "HEAD..origin/master"): "3",
        ("rev-list", "--count", "origin/master..HEAD"): "0",
    }

    def fake_run_git(args, *, check=True):
        return responses[tuple(args)]

    monkeypatch.setattr(runtime, "_run_git", fake_run_git)

    payload = runtime.build_patch_status(refresh=True)

    assert payload["status"] == "update_available"
    assert payload["update_available"] is True
    assert payload["behind_count"] == 3
    assert payload["ahead_count"] == 0
    assert payload["upstream_ref"] == "origin/master"
    assert payload["refresh_attempted"] is True


def test_patch_control_runtime_falls_back_to_origin_master_when_upstream_missing(monkeypatch, tmp_path):
    runtime = PatchControlRuntime(repo_root=tmp_path, utc_now_iso=lambda: "2026-03-13T10:00:00+09:00")
    (tmp_path / ".git").mkdir()

    def fake_run_git(args, *, check=True):
        key = tuple(args)
        if key == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "feature"
        if key == ("rev-parse", "HEAD"):
            return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        if key == ("log", "--format=%s", "-n", "1", "HEAD"):
            return "local commit"
        if key == ("status", "--porcelain"):
            return " M README.md"
        if key == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            raise subprocess.CalledProcessError(returncode=128, cmd=["git", *args], stderr="no upstream")
        if key == ("rev-parse", "origin/feature"):
            raise subprocess.CalledProcessError(returncode=128, cmd=["git", *args], stderr="missing")
        if key == ("rev-parse", "origin/master"):
            return "cccccccccccccccccccccccccccccccccccccccc"
        if key == ("log", "--format=%s", "-n", "1", "origin/master"):
            return "remote master"
        if key == ("rev-list", "--count", "HEAD..origin/master"):
            return "0"
        if key == ("rev-list", "--count", "origin/master..HEAD"):
            return "2"
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(runtime, "_run_git", fake_run_git)

    payload = runtime.build_patch_status(refresh=False)

    assert payload["status"] == "up_to_date"
    assert payload["update_available"] is False
    assert payload["upstream_ref"] == "origin/master"
    assert payload["working_tree_dirty"] is True
    assert payload["ahead_count"] == 2


def test_patch_control_runtime_returns_unavailable_when_repo_missing(tmp_path):
    runtime = PatchControlRuntime(repo_root=tmp_path, utc_now_iso=lambda: "2026-03-13T10:00:00+09:00")

    payload = runtime.build_patch_status(refresh=False)

    assert payload["status"] == "unavailable"
    assert payload["update_available"] is False
