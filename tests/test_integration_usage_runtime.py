from __future__ import annotations

import json
from pathlib import Path

from app.integration_usage_runtime import IntegrationUsageRuntime
from app.models import IntegrationRegistryRecord, JobRecord, JobStatus, RuntimeInputRecord, utc_now_iso
from app.store import SQLiteJobStore


def _make_job(job_id: str = "job-integration-usage") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=41,
        issue_title="지도 기능 추가",
        issue_url="https://github.com/owner/repo/issues/41",
        status=JobStatus.RUNNING.value,
        stage="implement_with_codex",
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/issue-41",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
        app_code="maps",
        source_repository="owner/repo",
    )


def test_append_usage_trail_event_writes_prompt_injected_integrations(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="gmaps-key",
            repository="owner/repo",
            app_code="maps",
            job_id="",
            scope="repository",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 키",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="provided",
            value="super-secret-value",
            requested_at=now,
            provided_at=now,
            updated_at=now,
        )
    )
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="지도 기능 가이드",
            implementation_guide_markdown="승인된 loader만 사용",
            verification_notes="지도 로딩 검증",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="approved",
            approval_note="도입 승인",
            approval_updated_at=now,
            approval_updated_by="operator",
            approval_trail=[],
        )
    )
    job = _make_job()
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    recommendation_path = docs_path / "INTEGRATION_RECOMMENDATIONS.json"
    recommendation_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "integration_id": "google_maps",
                        "display_name": "Google Maps",
                        "recommendation_status": "approved_candidate",
                        "matched_keywords": ["지도", "maps"],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime = IntegrationUsageRuntime(
        store=store,
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
    )
    paths = {
        "integration_recommendations": recommendation_path,
        "integration_guide_summary": docs_path / "INTEGRATION_GUIDE_SUMMARY.md",
        "integration_code_patterns": docs_path / "INTEGRATION_CODE_PATTERNS.md",
        "integration_verification_checklist": docs_path / "INTEGRATION_VERIFICATION_CHECKLIST.md",
        "integration_usage_trail": docs_path / "INTEGRATION_USAGE_TRAIL.json",
    }
    prompt_path = docs_path / "CODER_PROMPT_IMPLEMENT.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")

    payload = runtime.append_usage_trail_event(
        job=job,
        repository_path=repository_path,
        paths=paths,
        stage="implement_with_codex",
        route="coder",
        prompt_path=prompt_path,
    )

    assert payload["active"] is True
    assert payload["event_count"] == 1
    saved = json.loads(paths["integration_usage_trail"].read_text(encoding="utf-8"))
    assert saved["event_count"] == 1
    assert saved["events"][0]["route"] == "coder"
    assert saved["events"][0]["items"][0]["integration_id"] == "google_maps"
    assert saved["events"][0]["items"][0]["usage_status"] == "prompt_injected"
    assert saved["events"][0]["items"][0]["required_input_summary"]["provided"] == 1


def test_append_usage_trail_event_skips_when_no_registry_or_candidates(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    job = _make_job("job-integration-usage-empty")
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    runtime = IntegrationUsageRuntime(
        store=store,
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
    )
    usage_path = docs_path / "INTEGRATION_USAGE_TRAIL.json"
    payload = runtime.append_usage_trail_event(
        job=job,
        repository_path=repository_path,
        paths={"integration_usage_trail": usage_path},
        stage="plan_with_gemini",
        route="planner",
        prompt_path=docs_path / "PLANNER_PROMPT.md",
    )

    assert payload["active"] is False
    assert usage_path.exists() is False
