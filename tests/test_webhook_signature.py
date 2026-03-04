"""Tests for webhook HMAC validation."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient



def _payload() -> dict:
    return {
        "action": "labeled",
        "label": {"name": "agent:run"},
        "repository": {"full_name": "owner/repo"},
        "issue": {
            "number": 10,
            "title": "Implement feature",
            "html_url": "https://github.com/owner/repo/issues/10",
        },
    }


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_rejects_invalid_signature(app_components):
    _, _, app = app_components
    client = TestClient(app)

    raw_body = json.dumps(_payload()).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=raw_body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401


def test_webhook_accepts_valid_signature(app_components):
    settings, _, app = app_components
    client = TestClient(app)

    raw_body = json.dumps(_payload()).encode("utf-8")
    response = client.post(
        "/webhooks/github",
        data=raw_body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _sign(settings.webhook_secret, raw_body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
