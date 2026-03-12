"""Tests for mobile app development mode prompt injection."""

from __future__ import annotations

from app.prompt_builder import build_coder_prompt, build_planner_prompt, build_reviewer_prompt


def test_planner_prompt_includes_mobile_app_ruleset() -> None:
    prompt = build_planner_prompt(
        spec_path="_docs/SPEC.md",
        review_path="_docs/REVIEW.md",
        improvement_plan_path="_docs/IMPROVEMENT_PLAN.md",
        improvement_loop_state_path="_docs/IMPROVEMENT_LOOP_STATE.json",
        next_improvement_tasks_path="_docs/NEXT_IMPROVEMENT_TASKS.json",
        followup_backlog_task_path="_docs/FOLLOWUP_BACKLOG_TASK.json",
        plan_path="_docs/PLAN.md",
        memory_selection_path="_docs/MEMORY_SELECTION.json",
        memory_context_path="_docs/MEMORY_CONTEXT.md",
        operator_inputs_path="_docs/OPERATOR_INPUTS.json",
    )

    assert "Expo managed / bare RN / 기존 구조 유지" in prompt
    assert "Jest + React Native Testing Library" in prompt
    assert "Android emulator / iOS simulator / manual only" in prompt


def test_coder_prompt_includes_mobile_app_ruleset() -> None:
    prompt = build_coder_prompt(
        coding_goal="React Native 화면 구현",
        plan_path="_docs/PLAN.md",
        review_path="_docs/REVIEW.md",
        design_path="_docs/DESIGN_SYSTEM.md",
        design_tokens_path="_docs/DESIGN_TOKENS.json",
        token_handoff_path="_docs/TOKEN_HANDOFF.md",
        publish_handoff_path="_docs/PUBLISH_HANDOFF.md",
        improvement_plan_path="_docs/IMPROVEMENT_PLAN.md",
        improvement_loop_state_path="_docs/IMPROVEMENT_LOOP_STATE.json",
        next_improvement_tasks_path="_docs/NEXT_IMPROVEMENT_TASKS.json",
        memory_selection_path="_docs/MEMORY_SELECTION.json",
        memory_context_path="_docs/MEMORY_CONTEXT.md",
        operator_inputs_path="_docs/OPERATOR_INPUTS.json",
    )

    assert "greenfield app이면 Expo managed workflow를 우선 고려" in prompt
    assert "safe area, keyboard overlap, loading/empty/error, offline/network failure" in prompt
    assert "runtime input registry/env bridge로만 연결" in prompt


def test_reviewer_prompt_includes_mobile_app_ruleset() -> None:
    prompt = build_reviewer_prompt(
        spec_path="_docs/SPEC.md",
        plan_path="_docs/PLAN.md",
        review_path="_docs/REVIEW.md",
    )

    assert "모바일 앱 개발 모드 규칙" in prompt
    assert "Detox는 기존 저장소가 쓰거나 안정화 단계일 때만 요구" in prompt
