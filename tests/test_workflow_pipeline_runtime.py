from __future__ import annotations

from pathlib import Path

from app.workflow_pipeline_runtime import WorkflowPipelineRuntime


def test_normalize_workflow_node_result_supports_dict_payload() -> None:
    result = WorkflowPipelineRuntime.normalize_workflow_node_result(
        {
            "event": "failure",
            "status": "failed",
            "message": "needs retry",
            "error_message": "boom",
        }
    )

    assert result == {
        "event": "failure",
        "status": "failed",
        "message": "needs retry",
        "error_message": "boom",
    }


def test_workflow_result_artifact_info_filters_missing_paths(tmp_path: Path) -> None:
    spec_path = tmp_path / "SPEC.md"
    plan_path = tmp_path / "PLAN.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    plan_path.write_text("# plan\n", encoding="utf-8")

    info = WorkflowPipelineRuntime.workflow_result_artifact_info(
        {
            "paths": {
                "review": tmp_path / "REVIEW.md",
                "plan": plan_path,
                "spec": spec_path,
            }
        }
    )

    assert info["keys"] == ["plan", "spec"]
    assert info["paths"] == sorted([str(plan_path), str(spec_path)])
