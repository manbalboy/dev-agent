"""Tests for webhook-driven job enqueue behavior."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient



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
