from __future__ import annotations

import json
from pathlib import Path

from app.integration_recommendation_runtime import IntegrationRecommendationRuntime
from app.models import IntegrationRegistryRecord, JobRecord, RuntimeInputRecord, utc_now_iso
from app.store import SQLiteJobStore
from app.workflow_resume import build_workflow_artifact_paths


def _make_job(job_id: str = "job-integration-recommendation") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=88,
        issue_title="지도 기반 매장 찾기 화면 추가",
        issue_url="https://github.com/owner/repo/issues/88",
        status="queued",
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-88-integration-recommendation",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_write_integration_recommendation_artifact_matches_registry_and_input_state(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["지도", "위치", "maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드",
            implementation_guide_markdown="구현 가이드",
            verification_notes="지도 로딩 확인",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="stripe",
            display_name="Stripe",
            category="payments",
            supported_app_types=["web", "api"],
            tags=["결제", "checkout"],
            required_env_keys=["STRIPE_SECRET_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="",
            implementation_guide_markdown="",
            verification_notes="",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-google-maps",
            repository="owner/repo",
            app_code="default",
            job_id="",
            scope="repository",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 기능 구현용",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="requested",
            value="",
            placeholder="나중에 입력",
            requested_by="operator",
            requested_at=now,
            provided_at=None,
            updated_at=now,
        )
    )

    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    paths = build_workflow_artifact_paths(repository_path)
    paths["spec_json"].write_text(
        json.dumps(
            {
                "app_type": "web",
                "goal": "지도 기반 매장 찾기 화면을 구현한다.",
                "scope_in": ["지도 로딩", "현재 위치 기반 매장 찾기"],
                "acceptance_criteria": ["지도와 핀이 정상적으로 보인다."],
                "raw_request": "지도, 위치, 매장 찾기 기능이 필요하다.",
                "issue": {"title": "지도 기반 매장 찾기 화면 추가"},
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    paths["spec"].write_text("# SPEC\n- 지도 기반 매장 찾기\n", encoding="utf-8")
    actor_logs: list[tuple[str, str, str]] = []
    runtime = IntegrationRecommendationRuntime(
        store=store,
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
        docs_file=lambda repo_path, name: repo_path / "_docs" / name,
    )

    payload = runtime.write_integration_recommendation_artifact(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["integration_id"] == "google_maps"
    assert item["recommendation_status"] == "operator_review_and_input_required"
    assert item["required_input_summary"] == {
        "total": 1,
        "provided": 0,
        "requested": 1,
        "missing": 0,
    }
    assert item["input_readiness_status"] == "input_requested"
    assert "준비 대기" in item["input_readiness_reason"]
    assert "지도" in item["matched_keywords"]
    artifact = json.loads(paths["integration_recommendations"].read_text(encoding="utf-8"))
    assert artifact["items"][0]["integration_id"] == "google_maps"
    assert any("google_maps" in message for _, _, message in actor_logs)


def test_write_integration_recommendation_artifact_marks_rejected_candidates(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web"],
            tags=["지도", "maps"],
            required_env_keys=[],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드",
            implementation_guide_markdown="구현 가이드",
            verification_notes="",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="rejected",
            approval_note="이번 라운드에서는 지도 기능 제외",
            approval_updated_at=now,
            approval_updated_by="operator",
        )
    )

    repository_path = tmp_path / "repo-rejected"
    repository_path.mkdir(parents=True)
    paths = build_workflow_artifact_paths(repository_path)
    paths["spec_json"].write_text(
        json.dumps({"app_type": "web", "goal": "지도 화면 추가", "raw_request": "지도 표시가 필요하다."}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    runtime = IntegrationRecommendationRuntime(
        store=store,
        append_actor_log=lambda *_args, **_kwargs: None,
        docs_file=lambda repo_path, name: repo_path / "_docs" / name,
    )

    payload = runtime.write_integration_recommendation_artifact(
        job=_make_job("job-rejected"),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job-rejected.log",
    )

    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["approval_status"] == "rejected"
    assert item["recommendation_status"] == "operator_rejected"
    assert item["input_readiness_status"] == "approval_rejected"
    assert item["approval_trail_count"] == 0


def test_write_integration_recommendation_artifact_includes_latest_approval_action(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web"],
            tags=["지도", "maps"],
            required_env_keys=[],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드",
            implementation_guide_markdown="구현 가이드",
            verification_notes="",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="approved",
            approval_note="도입 승인",
            approval_updated_at=now,
            approval_updated_by="operator",
            approval_trail=[
                {
                    "action": "approve",
                    "source": "dashboard",
                    "previous_status": "pending",
                    "current_status": "approved",
                    "note": "도입 승인",
                    "acted_by": "operator",
                    "acted_at": now,
                }
            ],
        )
    )

    repository_path = tmp_path / "repo-approved"
    repository_path.mkdir(parents=True)
    paths = build_workflow_artifact_paths(repository_path)
    paths["spec_json"].write_text(
        json.dumps({"app_type": "web", "goal": "지도 화면 추가", "raw_request": "지도 표시가 필요하다."}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    runtime = IntegrationRecommendationRuntime(
        store=store,
        append_actor_log=lambda *_args, **_kwargs: None,
        docs_file=lambda repo_path, name: repo_path / "_docs" / name,
    )

    payload = runtime.write_integration_recommendation_artifact(
        job=_make_job("job-approved"),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job-approved.log",
    )

    item = payload["items"][0]
    assert item["approval_status"] == "approved"
    assert item["approval_trail_count"] == 1
    assert item["latest_approval_action"]["action"] == "approve"
