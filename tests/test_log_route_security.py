"""Security tests for /logs endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient



def test_log_route_serves_existing_file(app_components):
    settings, _, app = app_components
    log_path = settings.logs_dir / "sample.log"
    log_path.write_text("hello log\n", encoding="utf-8")

    client = TestClient(app)
    response = client.get("/logs/sample.log")

    assert response.status_code == 200
    assert "hello log" in response.text


def test_log_route_blocks_traversal_patterns(app_components):
    _, _, app = app_components
    client = TestClient(app)

    traversal = client.get("/logs/..%2Fjobs.json")
    assert traversal.status_code in {400, 404}

    invalid = client.get("/logs/bad*name.log")
    assert invalid.status_code == 400
