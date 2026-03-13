from __future__ import annotations

from pathlib import Path

from app.integration_guide_runtime import IntegrationGuideRuntime
from app.models import IntegrationRegistryRecord, RuntimeInputRecord, utc_now_iso
from app.store import SQLiteJobStore


def test_write_prompt_safe_guide_summary_artifact_includes_only_approved_integrations(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드: 지도 기능에서는 Google Maps를 우선 검토합니다.",
            implementation_guide_markdown="구현 가이드: 승인된 로더와 env 이름을 사용합니다.",
            verification_notes="지도 로딩과 marker 렌더를 검증합니다.",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="approved",
            approval_note="도입 승인",
            approval_updated_at=now,
            approval_updated_by="operator_a",
            approval_trail=[
                {
                    "action": "approve",
                    "source": "operator",
                    "previous_status": "pending",
                    "current_status": "approved",
                    "note": "도입 승인",
                    "acted_by": "operator_a",
                    "acted_at": now,
                }
            ],
        )
    )
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="stripe",
            display_name="Stripe",
            category="payments",
            supported_app_types=["web"],
            tags=["payments"],
            required_env_keys=["STRIPE_SECRET_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="결제 가이드",
            implementation_guide_markdown="Stripe 가이드",
            verification_notes="결제 확인",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="rejected",
            approval_note="이번 라운드 제외",
            approval_updated_at=now,
            approval_updated_by="operator_b",
            approval_trail=[
                {
                    "action": "reject",
                    "source": "operator",
                    "previous_status": "pending",
                    "current_status": "rejected",
                    "note": "이번 라운드 제외",
                    "acted_by": "operator_b",
                    "acted_at": now,
                }
            ],
        )
    )
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="gmaps-key",
            repository="owner/repo",
            app_code="default",
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

    runtime = IntegrationGuideRuntime(
        store=store,
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
    )
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    output_path = docs_path / "INTEGRATION_GUIDE_SUMMARY.md"

    payload = runtime.write_prompt_safe_guide_summary_artifact(
        repository_path=repository_path,
        paths={"integration_guide_summary": output_path},
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["count"] == 1
    assert "Google Maps" in text
    assert "GOOGLE_MAPS_API_KEY" in text
    assert "super-secret-value" not in text
    assert "Stripe" not in text
    assert "approve / operator_a" in text


def test_write_code_pattern_hint_artifact_includes_redacted_snippets(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web"],
            tags=["maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드",
            implementation_guide_markdown="""
- `MapLoader` 래퍼를 통해 script 로딩을 한 곳에서 처리합니다.
- env는 `process.env.GOOGLE_MAPS_API_KEY`로만 참조합니다.

```ts
const apiKey = "super-secret-value";
const map = loadGoogleMap({ apiKey });
```
""",
            verification_notes="""
- 지도 로딩 성공을 검증합니다.
- key 누락 시 fallback 안내를 검증합니다.
""",
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
    runtime = IntegrationGuideRuntime(
        store=store,
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
    )
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    output_path = docs_path / "INTEGRATION_CODE_PATTERNS.md"

    payload = runtime.write_code_pattern_hint_artifact(
        repository_path=repository_path,
        paths={"integration_code_patterns": output_path},
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["count"] == 1
    assert "MapLoader" in text
    assert "지도 로딩 성공을 검증합니다." in text
    assert "<REDACTED>" in text
    assert "super-secret-value" not in text


def test_write_verification_checklist_artifact_includes_checkboxes(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드",
            implementation_guide_markdown="구현 가이드",
            verification_notes="""
- 지도 로더가 정상 로딩되는지 확인합니다.
- marker가 기본 좌표에 렌더되는지 확인합니다.
- key 누락 시 fallback 안내가 보이는지 확인합니다.
""",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="approved",
            approval_note="승인",
            approval_updated_at=now,
            approval_updated_by="operator",
            approval_trail=[],
        )
    )
    runtime = IntegrationGuideRuntime(
        store=store,
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
    )
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    output_path = docs_path / "INTEGRATION_VERIFICATION_CHECKLIST.md"

    payload = runtime.write_verification_checklist_artifact(
        repository_path=repository_path,
        paths={"integration_verification_checklist": output_path},
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["count"] == 1
    assert "INTEGRATION_VERIFICATION_CHECKLIST" in text
    assert "- [ ] 지도 로더가 정상 로딩되는지 확인합니다." in text
    assert "- [ ] key 누락 시 fallback 안내가 보이는지 확인합니다." in text
