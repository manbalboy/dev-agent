from __future__ import annotations

from app.requeue_reason_runtime import build_requeue_reason_summary


def test_build_requeue_reason_summary_for_manual_resume_includes_target_node() -> None:
    summary = build_requeue_reason_summary(
        source="dashboard_manual_retry",
        reason_code="manual_resume_requeue",
        reason="운영자가 특정 노드부터 다시 실행합니다.",
        decision="manual_resume_requeue",
        recovery_status="manual_resume_queued",
        generated_at="2026-03-13T00:00:00+00:00",
        details={
            "target_node_id": "n12",
            "operator_note": "테스트 노드부터 다시 확인",
        },
    )

    assert summary["active"] is True
    assert summary["trigger"] == "operator_manual_retry"
    assert summary["retry_from_scratch"] is False
    assert summary["target_node_id"] == "n12"
    assert "(시작 노드: n12)" in summary["summary"]
