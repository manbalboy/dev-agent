"""Workflow node executor registry definitions."""

from __future__ import annotations

from typing import Dict, Set


WORKFLOW_NODE_HANDLER_NAMES: Dict[str, str] = {
    "gh_read_issue": "_workflow_node_read_issue",
    "if_label_match": "_workflow_node_if_label_match",
    "loop_until_pass": "_workflow_node_loop_until_pass",
    "write_spec": "_workflow_node_write_spec",
    "gemini_plan": "_workflow_node_gemini_plan",
    "idea_to_product_brief": "_workflow_node_idea_to_product_brief",
    "generate_user_flows": "_workflow_node_generate_user_flows",
    "define_mvp_scope": "_workflow_node_define_mvp_scope",
    "architecture_planning": "_workflow_node_architecture_planning",
    "project_scaffolding": "_workflow_node_project_scaffolding",
    "designer_task": "_workflow_node_designer_task",
    "publisher_task": "_workflow_node_publisher_task",
    "copywriter_task": "_workflow_node_copywriter_task",
    "documentation_task": "_workflow_node_documentation_task",
    "codex_implement": "_workflow_node_codex_implement",
    "code_change_summary": "_workflow_node_code_change_summary",
    "test_after_implement": "_workflow_node_test_after_implement",
    "tester_task": "_workflow_node_tester_task",
    "commit_implement": "_workflow_node_commit_implement",
    "gemini_review": "_workflow_node_gemini_review",
    "product_review": "_workflow_node_product_review",
    "improvement_stage": "_workflow_node_improvement_stage",
    "codex_fix": "_workflow_node_codex_fix",
    "coder_fix_from_test_report": "_workflow_node_coder_fix_from_test_report",
    "test_after_fix": "_workflow_node_test_after_fix",
    "tester_run_e2e": "_workflow_node_tester_run_e2e",
    "ux_e2e_review": "_workflow_node_ux_e2e_review",
    "test_after_fix_final": "_workflow_node_test_after_fix_final",
    "tester_retest_e2e": "_workflow_node_tester_retest_e2e",
    "commit_fix": "_workflow_node_commit_fix",
    "push_branch": "_workflow_node_push_branch",
    "create_pr": "_workflow_node_create_pr",
}

WORKFLOW_NODE_SKIP_AUTO_COMMIT: Set[str] = {"push_branch", "create_pr", "if_label_match", "loop_until_pass"}
