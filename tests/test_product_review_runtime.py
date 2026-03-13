"""Unit tests for product-review runtime helpers."""

from __future__ import annotations

from app.product_review_runtime import ProductReviewRuntime


def test_product_review_runtime_build_operating_principle_alignment_marks_design_first_blocked():
    alignment = ProductReviewRuntime.build_operating_principle_alignment(
        product_brief_exists=True,
        user_flows_exists=False,
        mvp_scope_exists=True,
        architecture_exists=True,
        mvp_has_out_of_scope=True,
        mvp_has_gates=True,
        flows_has_primary=True,
        flows_has_entry_exit=True,
        review_exists=True,
        ux_review_exists=True,
        test_report_count=1,
        todo_items_count=2,
        priority_summary={"P1": 2},
        candidate_count=4,
        scores={
            "usability": 4,
            "ux_clarity": 4,
            "error_state_handling": 4,
            "empty_state_handling": 4,
            "loading_state_handling": 4,
        },
        overall=4.0,
    )

    assert alignment["principle_2_design_first"]["status"] == "blocked"
    assert alignment["principle_7_product_quality_bar"]["status"] == "aligned"


def test_product_review_runtime_summarize_operating_policy_sets_flags():
    summary = ProductReviewRuntime.summarize_operating_policy(
        {
            "principle_1_mvp_first": {"status": "blocked"},
            "principle_4_evaluation_first": {"status": "warning"},
            "principle_6_no_repeat_same_fix": {"status": "runtime"},
        }
    )

    assert summary["blocked_principles"] == ["principle_1_mvp_first"]
    assert summary["warning_principles"] == ["principle_4_evaluation_first"]
    assert summary["runtime_principles"] == ["principle_6_no_repeat_same_fix"]
    assert summary["requires_scope_reset"] is True
    assert summary["requires_quality_focus"] is True


def test_product_review_runtime_validate_payload_rejects_invalid_shape():
    result = ProductReviewRuntime.validate_product_review_payload(
        {
            "scores": {"overall": 7},
            "findings": [],
            "improvement_candidates": {},
            "quality_gate": {},
        }
    )

    assert result["passed"] is False
    assert "scores.overall out of range" in " ".join(result["errors"])
    assert "findings must be non-empty array" in result["errors"]
    assert "improvement_candidates must be array" in result["errors"]
    assert "principle_alignment must be non-empty object" in result["errors"]
