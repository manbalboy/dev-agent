"""Tests for webhook-driven job enqueue behavior."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.github_webhook as github_webhook



def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _issue_payload(repository: str = "owner/repo") -> dict:
    return {
        "action": "labeled",
        "label": {"name": "agent:run"},
        "repository": {"full_name": repository},
        "issue": {
            "number": 77,
            "title": "Queue me",
            "html_url": f"https://github.com/{repository}/issues/77",
        },
    }


def _write_workflow_files(tmp_path: Path) -> tuple[Path, Path]:
    workflows_path = tmp_path / "config" / "workflows.json"
    apps_path = tmp_path / "config" / "apps.json"
    workflows_path.parent.mkdir(parents=True, exist_ok=True)
    workflows_path.write_text(
        json.dumps(
            {
                "default_workflow_id": "wf-default",
                "workflows": [
                    {"workflow_id": "wf-default", "entry_node_id": "n1", "nodes": [{"id": "n1", "type": "gh_read_issue"}], "edges": []},
                    {"workflow_id": "wf-web", "entry_node_id": "n1", "nodes": [{"id": "n1", "type": "gh_read_issue"}], "edges": []},
                    {"workflow_id": "wf-job", "entry_node_id": "n1", "nodes": [{"id": "n1", "type": "gh_read_issue"}], "edges": []},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    apps_path.write_text(
        json.dumps(
            [
                {
                    "code": "default",
                    "name": "Default",
                    "repository": "owner/repo",
                    "workflow_id": "wf-default",
                },
                {
                    "code": "web",
                    "name": "Web",
                    "repository": "owner/repo",
                    "workflow_id": "wf-web",
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return apps_path, workflows_path


def test_enqueue_job_on_agent_run_label(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    body = json.dumps(_issue_payload()).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sign(settings.webhook_secret, body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True

    queued_id = payload["job_id"]
    stored = store.get_job(queued_id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.stage == "queued"
    assert store.queue_size() == 1


def test_ignore_disallowed_repository(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    body = json.dumps(_issue_payload(repository="another/repo")).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sign(settings.webhook_secret, body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["reason"] == "repository_not_allowed"
    assert store.queue_size() == 0


def test_enqueue_long_track_uses_stable_issue_branch(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    payload = _issue_payload()
    payload["issue"]["title"] = "[장기] Keep branch"
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sign(settings.webhook_secret, body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    queued_id = response.json()["job_id"]
    stored = store.get_job(queued_id)
    assert stored is not None
    assert stored.track == "long"
    assert stored.branch_name == "agenthub/default/issue-77"


def test_enqueue_ultra_track_uses_stable_issue_branch(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    payload = _issue_payload()
    payload["issue"]["title"] = "[초장기] Keep branch"
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sign(settings.webhook_secret, body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    queued_id = response.json()["job_id"]
    stored = store.get_job(queued_id)
    assert stored is not None
    assert stored.track == "ultra"
    assert stored.branch_name == "agenthub/default/issue-77"


def test_webhook_resolves_workflow_from_app_mapping(app_components, monkeypatch, tmp_path: Path):
    settings, store, app = app_components
    client = TestClient(app)
    apps_path, workflows_path = _write_workflow_files(tmp_path)
    monkeypatch.setattr(github_webhook, "_APPS_CONFIG_PATH", apps_path)
    monkeypatch.setattr(github_webhook, "_WORKFLOWS_CONFIG_PATH", workflows_path)

    payload = _issue_payload()
    payload["issue"]["labels"] = [{"name": "app:web"}]
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sign(settings.webhook_secret, body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["workflow_id"] == "wf-web"
    assert data["workflow_source"] == "app"

    stored = store.get_job(data["job_id"])
    assert stored is not None
    assert stored.workflow_id == "wf-web"


def test_webhook_rejects_unknown_requested_workflow_id(app_components, monkeypatch, tmp_path: Path):
    settings, store, app = app_components
    client = TestClient(app)
    apps_path, workflows_path = _write_workflow_files(tmp_path)
    monkeypatch.setattr(github_webhook, "_APPS_CONFIG_PATH", apps_path)
    monkeypatch.setattr(github_webhook, "_WORKFLOWS_CONFIG_PATH", workflows_path)

    payload = _issue_payload()
    payload["issue"]["labels"] = [{"name": "workflow:does-not-exist"}]
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sign(settings.webhook_secret, body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["reason"] == "invalid_workflow_id"
    assert store.queue_size() == 0
