"""Improvement-stage runtime extraction for orchestrator."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.models import JobRecord, JobStage, utc_now_iso


class ImprovementRuntime:
    """Encapsulate improvement-stage planning without changing external contracts."""

    def __init__(
        self,
        *,
        set_stage: Callable[[str, JobStage, Path], None],
        docs_file: Callable[[Path, str], Path],
        read_json_file: Callable[[Path | None], Dict[str, Any]],
        execute_shell_command,
        actor_log_writer,
        append_actor_log: Callable[[Path, str, str], None],
        write_structured_memory_artifacts,
        write_memory_retrieval_artifacts,
        write_strategy_shadow_report,
        ingest_memory_runtime_artifacts,
        build_improvement_strategy_inputs,
        select_improvement_strategy,
        select_next_improvement_items,
    ) -> None:
        self.set_stage = set_stage
        self.docs_file = docs_file
        self.read_json_file = read_json_file
        self.execute_shell_command = execute_shell_command
        self.actor_log_writer = actor_log_writer
        self.append_actor_log = append_actor_log
        self.write_structured_memory_artifacts = write_structured_memory_artifacts
        self.write_memory_retrieval_artifacts = write_memory_retrieval_artifacts
        self.write_strategy_shadow_report = write_strategy_shadow_report
        self.ingest_memory_runtime_artifacts = ingest_memory_runtime_artifacts
        self.build_improvement_strategy_inputs_fn = build_improvement_strategy_inputs
        self.select_improvement_strategy_fn = select_improvement_strategy
        self.select_next_improvement_items_fn = select_next_improvement_items

    def stage_improvement_stage(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create next-loop improvement plan and loop guard signals."""

        self.set_stage(job.job_id, JobStage.IMPROVEMENT_STAGE, log_path)
        product_review_path = paths.get("product_review", self.docs_file(repository_path, "PRODUCT_REVIEW.json"))
        review_payload = self.read_json_file(product_review_path)
        review_history_path = paths.get("review_history", self.docs_file(repository_path, "REVIEW_HISTORY.json"))
        history_payload = self.read_json_file(review_history_path)
        history_entries = history_payload.get("entries", []) if isinstance(history_payload, dict) else []
        if not isinstance(history_entries, list):
            history_entries = []
        backlog_payload = self.read_json_file(paths.get("improvement_backlog"))
        backlog_items = backlog_payload.get("items", []) if isinstance(backlog_payload, dict) else []
        if not isinstance(backlog_items, list):
            backlog_items = []
        maturity_payload = self.read_json_file(paths.get("repo_maturity"))
        trend_payload = self.read_json_file(paths.get("quality_trend"))
        operating_policy = review_payload.get("operating_policy", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(operating_policy, dict):
            operating_policy = {}

        top_issue_id = str(backlog_items[0].get("id", "")) if backlog_items else ""
        recent_top_ids = [str(item.get("top_issue_ids", [""])[0]) for item in history_entries[-3:] if item.get("top_issue_ids")]
        repeated_issue_limit_hit = bool(top_issue_id) and recent_top_ids.count(top_issue_id) >= 2

        recent_scores = [float(item.get("overall", 0.0)) for item in history_entries[-3:] if item.get("overall") is not None]
        score_stagnation_detected = len(recent_scores) >= 3 and (max(recent_scores) - min(recent_scores) <= 0.15)
        quality_regression_detected = False
        if len(history_entries) >= 2:
            prev = float(history_entries[-2].get("overall", 0.0))
            current = float(history_entries[-1].get("overall", 0.0))
            quality_regression_detected = current < (prev - 0.2)
        design_reset_required = bool(operating_policy.get("requires_design_reset"))
        scope_reset_required = bool(operating_policy.get("requires_scope_reset"))
        quality_focus_required = bool(operating_policy.get("requires_quality_focus"))
        strategy_change_required = (
            repeated_issue_limit_hit
            or score_stagnation_detected
            or quality_regression_detected
            or design_reset_required
            or scope_reset_required
        )

        git_head = ""
        result = self.execute_shell_command(
            command=f"git -C {shlex.quote(str(repository_path))} rev-parse HEAD",
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "GIT"),
            check=False,
            command_purpose="read current git head",
        )
        if int(getattr(result, "exit_code", 1)) == 0:
            git_head = str(getattr(result, "stdout", "")).strip()

        loop_state = {
            "generated_at": utc_now_iso(),
            "same_issue_repeat_limit": 2,
            "repeated_issue_limit_hit": repeated_issue_limit_hit,
            "score_stagnation_detected": score_stagnation_detected,
            "quality_regression_detected": quality_regression_detected,
            "strategy_change_required": strategy_change_required,
            "principle_enforcement": {
                "blocked_principles": operating_policy.get("blocked_principles", []),
                "warning_principles": operating_policy.get("warning_principles", []),
                "requires_design_reset": design_reset_required,
                "requires_scope_reset": scope_reset_required,
                "requires_quality_focus": quality_focus_required,
            },
            "rollback": {
                "last_known_head": git_head,
                "rollback_candidate": bool(git_head),
            },
            "strategy": "normal_iterative_improvement",
        }
        loop_state_path = paths.get("improvement_loop_state", self.docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))
        loop_state_path.write_text(
            json.dumps(loop_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        overall_score = float(review_payload.get("scores", {}).get("overall", 0.0)) if isinstance(review_payload, dict) else 0.0
        scores_payload = review_payload.get("scores", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(scores_payload, dict):
            scores_payload = {}
        artifact_health = review_payload.get("artifact_health", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(artifact_health, dict):
            artifact_health = {}
        categories_below = (
            review_payload.get("quality_gate", {}).get("categories_below_threshold", [])
            if isinstance(review_payload, dict)
            else []
        )
        if not isinstance(categories_below, list):
            categories_below = []
        quality_gate_payload = review_payload.get("quality_gate", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(quality_gate_payload, dict):
            quality_gate_payload = {}

        strategy_inputs = self.build_improvement_strategy_inputs_fn(
            review_payload=review_payload,
            maturity_payload=maturity_payload,
            trend_payload=trend_payload,
            categories_below=categories_below,
        )
        strategy_decision = self.select_improvement_strategy_fn(
            overall_score=overall_score,
            strategy_inputs=strategy_inputs,
            repeated_issue_limit_hit=repeated_issue_limit_hit,
            score_stagnation_detected=score_stagnation_detected,
            quality_regression_detected=quality_regression_detected,
            design_reset_required=design_reset_required,
            scope_reset_required=scope_reset_required,
            quality_focus_required=quality_focus_required,
        )

        strategy = str(strategy_decision.get("strategy", "normal_iterative_improvement")).strip() or "normal_iterative_improvement"
        next_scope_restriction = str(strategy_decision.get("next_scope_restriction", "normal")).strip() or "normal"
        strategy_focus = str(strategy_decision.get("focus", "balanced")).strip() or "balanced"
        strategy_mode_shift = strategy != "normal_iterative_improvement"

        loop_state["strategy"] = strategy
        loop_state["strategy_focus"] = strategy_focus
        rollback_recommended = quality_regression_detected and bool(git_head)
        loop_state["next_scope_restriction"] = next_scope_restriction
        loop_state["rollback_recommended"] = rollback_recommended
        loop_state["categories_below_threshold"] = categories_below
        loop_state["overall_score"] = overall_score
        loop_state["strategy_inputs"] = strategy_inputs
        change_reasons = self.build_strategy_change_reasons(
            top_issue_id=top_issue_id,
            repeated_issue_limit_hit=repeated_issue_limit_hit,
            score_stagnation_detected=score_stagnation_detected,
            quality_regression_detected=quality_regression_detected,
            design_reset_required=design_reset_required,
            scope_reset_required=scope_reset_required,
            quality_focus_required=quality_focus_required,
            recent_scores=recent_scores,
            history_entries=history_entries,
            strategy_decision=strategy_decision,
        )
        loop_state["strategy_change_reasons"] = change_reasons
        loop_state_path.write_text(
            json.dumps(loop_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if design_reset_required:
            next_items = [
                {
                    "id": "policy_design_rebaseline",
                    "priority": "P0",
                    "title": "제품 정의/설계 문서 재정렬",
                    "reason": "설계 선행 원칙 위반 또는 문서 계약 약화 감지",
                    "action": "PRODUCT_BRIEF/USER_FLOWS/MVP_SCOPE/ARCHITECTURE_PLAN을 재정리한 뒤 다시 계획 수립",
                }
            ]
        else:
            next_items = self.select_next_improvement_items_fn(
                strategy=strategy,
                backlog_items=backlog_items,
                categories_below=categories_below,
                scores=scores_payload,
                artifact_health=artifact_health,
                quality_gate=quality_gate_payload,
            )
        next_tasks_payload = {
            "generated_at": utc_now_iso(),
            "strategy": loop_state.get("strategy", "normal_iterative_improvement"),
            "strategy_focus": strategy_focus,
            "scope_restriction": next_scope_restriction,
            "strategy_inputs": strategy_inputs,
            "tasks": [
                {
                    "task_id": f"next_{index + 1}",
                    "source_issue_id": str(item.get("id", "")),
                    "title": str(item.get("title", "")),
                    "priority": str(item.get("priority", "P2")),
                    "reason": str(item.get("reason", "")),
                    "action": str(item.get("action", "")),
                    "selected_by_strategy": strategy,
                    "recommended_node_type": (
                        "gemini_plan"
                        if design_reset_required or scope_reset_required
                        else "coder_fix_from_test_report"
                        if str(item.get("priority", "P2")) in {"P0", "P1"}
                        else "gemini_plan"
                    ),
                }
                for index, item in enumerate(next_items)
            ],
        }
        next_tasks_path = paths.get(
            "next_improvement_tasks",
            self.docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"),
        )
        next_tasks_path.write_text(
            json.dumps(next_tasks_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        improvement_plan_path = paths.get("improvement_plan", self.docs_file(repository_path, "IMPROVEMENT_PLAN.md"))
        improvement_plan_path.write_text(
            "\n".join(
                self.build_improvement_plan_lines(
                    loop_state=loop_state,
                    overall_score=overall_score,
                    next_scope_restriction=next_scope_restriction,
                    strategy_focus=strategy_focus,
                    repeated_issue_limit_hit=repeated_issue_limit_hit,
                    score_stagnation_detected=score_stagnation_detected,
                    quality_regression_detected=quality_regression_detected,
                    strategy_change_required=strategy_change_required,
                    rollback_recommended=rollback_recommended,
                    strategy_inputs=strategy_inputs,
                    change_reasons=change_reasons,
                    next_items=next_items,
                    categories_below=categories_below,
                    git_head=git_head,
                    next_tasks_path=next_tasks_path,
                    strategy_mode_shift=strategy_mode_shift,
                )
            ),
            encoding="utf-8",
        )
        self.write_structured_memory_artifacts(
            job=job,
            repository_path=repository_path,
            paths=paths,
            review_payload=review_payload,
            maturity_payload=maturity_payload,
            trend_payload=trend_payload,
            loop_state=loop_state,
            next_tasks_payload=next_tasks_payload,
        )
        self.write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        self.write_strategy_shadow_report(
            job=job,
            repository_path=repository_path,
            paths=paths,
            strategy_inputs=strategy_inputs,
            selected_strategy=strategy,
            selected_focus=strategy_focus,
        )
        self.ingest_memory_runtime_artifacts(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"IMPROVEMENT_PLAN.md 생성 완료 — strategy={loop_state['strategy']}, "
            f"next_scope={next_scope_restriction}, rollback={rollback_recommended}",
        )

    @staticmethod
    def build_improvement_strategy_inputs(
        *,
        review_payload: Dict[str, Any],
        maturity_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        categories_below: List[str],
    ) -> Dict[str, Any]:
        """Collect strategy-selection inputs in one explicit structure."""

        scores = review_payload.get("scores", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(scores, dict):
            scores = {}
        artifact_health = review_payload.get("artifact_health", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(artifact_health, dict):
            artifact_health = {}
        tests_info = artifact_health.get("tests", {}) if isinstance(artifact_health, dict) else {}
        if not isinstance(tests_info, dict):
            tests_info = {}
        quality_gate = review_payload.get("quality_gate", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(quality_gate, dict):
            quality_gate = {}
        persistent_low_categories = trend_payload.get("persistent_low_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(persistent_low_categories, list):
            persistent_low_categories = []
        stagnant_categories = trend_payload.get("stagnant_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(stagnant_categories, list):
            stagnant_categories = []

        ux_categories = {
            "usability",
            "ux_clarity",
            "error_state_handling",
            "empty_state_handling",
            "loading_state_handling",
        }
        engineering_categories = {"architecture_structure", "maintainability", "code_quality"}

        return {
            "maturity_level": str(maturity_payload.get("level", "bootstrap") or "bootstrap"),
            "maturity_progression": str(
                trend_payload.get("maturity_progression", maturity_payload.get("progression", "unchanged")) or "unchanged"
            ),
            "quality_trend_direction": str(trend_payload.get("trend_direction", "stable") or "stable"),
            "review_round_count": int(trend_payload.get("review_round_count", 0) or 0),
            "quality_gate_passed": bool(quality_gate.get("passed")),
            "categories_below": list(categories_below),
            "persistent_low_categories": list(persistent_low_categories),
            "stagnant_categories": list(stagnant_categories),
            "has_test_gap": (
                "test_coverage" in categories_below
                or "test_coverage" in persistent_low_categories
                or "test_coverage" in stagnant_categories
                or int(tests_info.get("test_file_count", 0) or 0) == 0
                or int(tests_info.get("report_count", 0) or 0) == 0
            ),
            "has_ux_gap": any(
                category in ux_categories
                for category in [*categories_below, *persistent_low_categories, *stagnant_categories]
            ),
            "has_engineering_gap": any(
                category in engineering_categories
                for category in [*categories_below, *persistent_low_categories, *stagnant_categories]
            ),
            "overall_score": float(scores.get("overall", 0.0) or 0.0),
            "test_score": int(scores.get("test_coverage", 0) or 0),
            "ux_score_floor": min(
                int(scores.get("usability", 0) or 0),
                int(scores.get("ux_clarity", 0) or 0),
                int(scores.get("error_state_handling", 0) or 0),
                int(scores.get("empty_state_handling", 0) or 0),
                int(scores.get("loading_state_handling", 0) or 0),
            ),
        }

    @staticmethod
    def select_improvement_strategy(
        *,
        overall_score: float,
        strategy_inputs: Dict[str, Any],
        repeated_issue_limit_hit: bool,
        score_stagnation_detected: bool,
        quality_regression_detected: bool,
        design_reset_required: bool,
        scope_reset_required: bool,
        quality_focus_required: bool,
    ) -> Dict[str, Any]:
        """Choose next-loop strategy from maturity/trend/policy signals."""

        maturity_level = str(strategy_inputs.get("maturity_level", "bootstrap") or "bootstrap")
        maturity_progression = str(strategy_inputs.get("maturity_progression", "unchanged") or "unchanged")
        trend_direction = str(strategy_inputs.get("quality_trend_direction", "stable") or "stable")
        review_round_count = int(strategy_inputs.get("review_round_count", 0) or 0)
        quality_gate_passed = bool(strategy_inputs.get("quality_gate_passed"))
        has_test_gap = bool(strategy_inputs.get("has_test_gap"))
        has_ux_gap = bool(strategy_inputs.get("has_ux_gap"))
        has_engineering_gap = bool(strategy_inputs.get("has_engineering_gap"))
        categories_below = strategy_inputs.get("categories_below", [])
        if not isinstance(categories_below, list):
            categories_below = []
        persistent_low_categories = strategy_inputs.get("persistent_low_categories", [])
        if not isinstance(persistent_low_categories, list):
            persistent_low_categories = []
        stagnant_categories = strategy_inputs.get("stagnant_categories", [])
        if not isinstance(stagnant_categories, list):
            stagnant_categories = []

        reasons: List[str] = []

        if design_reset_required:
            return {
                "strategy": "design_rebaseline",
                "next_scope_restriction": "MVP_redefinition",
                "focus": "design",
                "reasons": ["제품 정의/설계 문서 재정렬이 우선입니다."],
            }
        if quality_regression_detected:
            return {
                "strategy": "rollback_or_stabilize",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": ["품질이 하락해 기능 확장보다 안정화와 복구가 우선입니다."],
            }
        if scope_reset_required:
            return {
                "strategy": "narrow_scope_stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "scope",
                "reasons": ["범위가 커졌기 때문에 MVP 범위 재정렬과 안정화가 필요합니다."],
            }

        if repeated_issue_limit_hit or score_stagnation_detected:
            if has_test_gap:
                reasons.append(
                    "반복/정체 구간에서 테스트 격차가 보입니다."
                    + (f" persistent_low={persistent_low_categories}" if "test_coverage" in persistent_low_categories else "")
                )
                return {
                    "strategy": "test_hardening",
                    "next_scope_restriction": "P1_only",
                    "focus": "testing",
                    "reasons": reasons,
                }
            if has_ux_gap:
                reasons.append("반복/정체 구간에서 UX 상태 처리 격차가 보여 화면 명확성 개선이 우선입니다.")
                return {
                    "strategy": "ux_clarity_improvement",
                    "next_scope_restriction": "P1_only",
                    "focus": "ux",
                    "reasons": reasons,
                }
            reasons.append("반복/정체 구간이므로 기능 확대보다 구조 안정화가 우선입니다.")
            return {
                "strategy": "stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": reasons,
            }

        if "test_coverage" in persistent_low_categories:
            return {
                "strategy": "test_hardening",
                "next_scope_restriction": "P1_only",
                "focus": "testing",
                "reasons": ["test_coverage가 최근 3라운드 연속 저점이라 테스트 강화가 우선입니다."],
            }

        if any(
            category in persistent_low_categories
            for category in {"ux_clarity", "usability", "error_state_handling", "empty_state_handling", "loading_state_handling"}
        ):
            return {
                "strategy": "ux_clarity_improvement",
                "next_scope_restriction": "P1_only",
                "focus": "ux",
                "reasons": [f"UX 관련 카테고리 저점이 지속됨: {', '.join(persistent_low_categories)}"],
            }

        if has_test_gap and (quality_focus_required or trend_direction in {"stable", "declining"} or review_round_count >= 2):
            return {
                "strategy": "test_hardening",
                "next_scope_restriction": "P1_only",
                "focus": "testing",
                "reasons": ["테스트/리포트 증거가 부족해 회귀 방지와 커버리지 보강이 우선입니다."],
            }

        if has_ux_gap:
            return {
                "strategy": "ux_clarity_improvement",
                "next_scope_restriction": "P1_only",
                "focus": "ux",
                "reasons": [f"UX 관련 저점 카테고리({', '.join(categories_below)})가 존재해 사용 흐름 명확화가 우선입니다."],
            }

        if quality_focus_required or overall_score < 3.0 or (maturity_level in {"bootstrap", "mvp"} and not quality_gate_passed):
            return {
                "strategy": "stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": ["현재 성숙도/품질 상태에서는 기능 확장보다 안정화가 우선입니다."],
            }

        if (
            any(category in stagnant_categories for category in {"code_quality", "architecture_structure", "maintainability"})
            and trend_direction in {"stable", "declining"}
        ):
            return {
                "strategy": "stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": [f"엔지니어링 카테고리 정체가 지속됨: {', '.join(stagnant_categories)}"],
            }

        if (
            quality_gate_passed
            and overall_score >= 3.6
            and trend_direction == "improving"
            and maturity_level in {"usable", "stable", "product_grade"}
            and not categories_below
            and not has_engineering_gap
        ):
            return {
                "strategy": "feature_expansion",
                "next_scope_restriction": "normal",
                "focus": "feature",
                "reasons": ["품질 게이트를 통과했고 추세가 상승 중이므로 다음 핵심 사용자 가치를 확장할 수 있습니다."],
            }

        return {
            "strategy": "normal_iterative_improvement",
            "next_scope_restriction": "normal",
            "focus": "balanced",
            "reasons": [
                f"성숙도={maturity_level}, 추세={trend_direction}, progression={maturity_progression} 기준에서 균형 개선을 유지합니다."
            ],
        }

    @staticmethod
    def select_next_improvement_items(
        *,
        strategy: str,
        backlog_items: List[Dict[str, Any]],
        categories_below: List[str],
        scores: Dict[str, Any],
        artifact_health: Dict[str, Any],
        quality_gate: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Choose strategy-aligned next tasks from backlog, with synthetic fallback."""

        if not isinstance(backlog_items, list):
            backlog_items = []
        if not isinstance(categories_below, list):
            categories_below = []

        def priority_rank(value: str) -> int:
            return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(str(value or "P3"), 3)

        def candidate_text(item: Dict[str, Any]) -> str:
            return " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("reason", "")),
                    str(item.get("action", "")),
                    str(item.get("source", "")),
                ]
            ).lower()

        def pick_by_keywords(keywords: List[str], *, allowed_priorities: set[str], limit: int = 3) -> List[Dict[str, Any]]:
            scored: List[tuple[int, int, Dict[str, Any]]] = []
            for item in backlog_items:
                text = candidate_text(item)
                match_count = sum(1 for keyword in keywords if keyword in text)
                if match_count <= 0:
                    continue
                priority = str(item.get("priority", "P3"))
                if priority not in allowed_priorities:
                    continue
                scored.append((-match_count, priority_rank(priority), item))
            scored.sort(key=lambda row: (row[0], row[1], str(row[2].get("title", ""))))
            return [row[2] for row in scored[:limit]]

        if strategy == "feature_expansion":
            selected = [item for item in backlog_items if str(item.get("priority", "P3")) in {"P1", "P2"}][:3]
            if selected:
                return selected
            return [
                {
                    "id": "strategy_feature_expansion",
                    "priority": "P1",
                    "title": "다음 핵심 사용자 가치 1개 확장",
                    "reason": "품질 게이트 통과 및 추세 상승 상태에서 기능 확장을 진행합니다.",
                    "action": "MVP_SCOPE 기준에서 사용자 가치가 높은 기능 1개만 추가 구현하고 테스트를 함께 보강",
                }
            ]

        if strategy == "test_hardening":
            selected = pick_by_keywords(
                ["test", "coverage", "regression", "spec", "e2e", "integration", "playwright", "pytest"],
                allowed_priorities={"P0", "P1", "P2"},
            )
            if selected:
                return selected
            tests_info = artifact_health.get("tests", {}) if isinstance(artifact_health, dict) else {}
            if not isinstance(tests_info, dict):
                tests_info = {}
            return [
                {
                    "id": "strategy_test_hardening",
                    "priority": "P1",
                    "title": "회귀 테스트 및 테스트 전략 보강",
                    "reason": (
                        f"test_file_count={int(tests_info.get('test_file_count', 0) or 0)}, "
                        f"report_count={int(tests_info.get('report_count', 0) or 0)}"
                    ),
                    "action": "핵심 사용자 흐름 기준 회귀 테스트를 추가하고 PLAN/리뷰 문서의 테스트 전략을 구체화",
                }
            ]

        if strategy == "ux_clarity_improvement":
            selected = pick_by_keywords(
                ["ux", "usability", "empty", "loading", "error", "ui", "flow", "copy", "message", "spinner", "skeleton"],
                allowed_priorities={"P0", "P1", "P2"},
            )
            if selected:
                return selected
            return [
                {
                    "id": "strategy_ux_clarity_improvement",
                    "priority": "P1",
                    "title": "UX 상태 처리와 화면 안내 문구 정리",
                    "reason": f"낮은 UX 관련 카테고리: {', '.join(categories_below) or 'ux_clarity'}",
                    "action": "error/empty/loading 상태 UI와 안내 문구를 정리하고 USER_FLOWS 기준으로 사용자 흐름을 더 명확하게 다듬기",
                }
            ]

        if strategy in {"stabilization", "rollback_or_stabilize", "narrow_scope_stabilization"}:
            selected = [item for item in backlog_items if str(item.get("priority", "P3")) in {"P0", "P1"}][:3]
            if selected:
                return selected
            return [
                {
                    "id": "strategy_stabilization",
                    "priority": "P1",
                    "title": "구조 안정화 및 회귀 방지 작업",
                    "reason": "품질 게이트 미통과 또는 구조적 약점이 남아 있습니다.",
                    "action": "기능 확장 없이 현재 저점 카테고리를 보강하고 회귀 테스트를 추가",
                }
            ]

        if strategy == "normal_iterative_improvement":
            return backlog_items[:5]

        return backlog_items[:3]

    @staticmethod
    def build_strategy_change_reasons(
        *,
        top_issue_id: str,
        repeated_issue_limit_hit: bool,
        score_stagnation_detected: bool,
        quality_regression_detected: bool,
        design_reset_required: bool,
        scope_reset_required: bool,
        quality_focus_required: bool,
        recent_scores: List[float],
        history_entries: List[Dict[str, Any]],
        strategy_decision: Dict[str, Any],
    ) -> List[str]:
        """Explain why the strategy changed so the loop state stays auditable."""

        change_reasons: List[str] = []
        if repeated_issue_limit_hit:
            change_reasons.append(f"동일 이슈 {top_issue_id!r}가 최근 3회 내 2회 이상 반복됨")
        if score_stagnation_detected:
            scores_str = ", ".join(f"{score:.2f}" for score in recent_scores)
            change_reasons.append(f"최근 3회 점수 정체 감지 ({scores_str}) — 변화폭 ≤ 0.15")
        if quality_regression_detected and len(history_entries) >= 2:
            prev_s = float(history_entries[-2].get("overall", 0.0))
            curr_s = float(history_entries[-1].get("overall", 0.0))
            change_reasons.append(f"품질 하락 감지: {prev_s:.2f} → {curr_s:.2f} (0.2 이상 하락)")
        if design_reset_required:
            change_reasons.append("설계 선행 원칙 위반: 제품 정의/설계 문서를 다시 정렬해야 함")
        if scope_reset_required:
            change_reasons.append("MVP 우선/작은 단위 개발 원칙 위반: 범위 축소 또는 재정의 필요")
        if quality_focus_required:
            change_reasons.append("평가 우선/안정성 보호 원칙 기준에서 품질 근거가 부족함")
        for reason in strategy_decision.get("reasons", []):
            if reason not in change_reasons:
                change_reasons.append(reason)
        return change_reasons

    @staticmethod
    def build_improvement_plan_lines(
        *,
        loop_state: Dict[str, Any],
        overall_score: float,
        next_scope_restriction: str,
        strategy_focus: str,
        repeated_issue_limit_hit: bool,
        score_stagnation_detected: bool,
        quality_regression_detected: bool,
        strategy_change_required: bool,
        rollback_recommended: bool,
        strategy_inputs: Dict[str, Any],
        change_reasons: List[str],
        next_items: List[Dict[str, Any]],
        categories_below: List[str],
        git_head: str,
        next_tasks_path: Path,
        strategy_mode_shift: bool,
    ) -> List[str]:
        """Render IMPROVEMENT_PLAN.md in one place."""

        plan_lines = [
            "# IMPROVEMENT PLAN",
            "",
            f"- Generated at: {loop_state['generated_at']}",
            f"- Strategy: `{loop_state['strategy']}`",
            f"- Current overall score: `{overall_score}`",
            f"- Next scope restriction: `{next_scope_restriction}`",
            "",
            "## Loop Guard Signals",
            f"- repeated_issue_limit_hit: `{repeated_issue_limit_hit}`",
            f"- score_stagnation_detected: `{score_stagnation_detected}`",
            f"- quality_regression_detected: `{quality_regression_detected}`",
            f"- strategy_change_required: `{strategy_change_required}`",
            f"- rollback_recommended: `{rollback_recommended}`",
            f"- strategy_focus: `{strategy_focus}`",
            "",
            "## Strategy Inputs",
            f"- maturity_level: `{strategy_inputs.get('maturity_level', '')}`",
            f"- maturity_progression: `{strategy_inputs.get('maturity_progression', '')}`",
            f"- quality_trend_direction: `{strategy_inputs.get('quality_trend_direction', '')}`",
            f"- review_round_count: `{strategy_inputs.get('review_round_count', 0)}`",
            f"- quality_gate_passed: `{strategy_inputs.get('quality_gate_passed', False)}`",
            f"- persistent_low_categories: `{', '.join(strategy_inputs.get('persistent_low_categories', [])) or '-'}`",
            f"- stagnant_categories: `{', '.join(strategy_inputs.get('stagnant_categories', [])) or '-'}`",
        ]
        if change_reasons:
            plan_lines.extend(["", "## Strategy Change Reasons"])
            for reason in change_reasons:
                plan_lines.append(f"- {reason}")

        plan_lines.extend(["", "## Next Improvements"])
        if strategy_change_required or strategy_mode_shift:
            plan_lines.append("> **전략 변경 모드**: P1 항목만 처리합니다. 범위를 축소하고 안정화 작업을 우선 수행하세요.")
        for item in next_items:
            action = str(item.get("action", "")).strip()
            plan_lines.append(
                f"- [{item.get('priority', 'P2')}] {str(item.get('title', '')).strip()}"
                + (f"\n  - 원인: {item.get('reason', '')}" if item.get("reason") else "")
                + (f"\n  - 액션: {action}" if action else "")
            )
        if not next_items:
            plan_lines.append("- 개선 백로그 항목 없음 (품질 목표 달성)")

        if categories_below:
            plan_lines.extend(["", "## Categories Below Threshold (≤2/5)"])
            for category in categories_below:
                plan_lines.append(f"- {category}")

        plan_lines.extend(
            [
                "",
                "## Recovery Option",
                f"- last_known_head: `{git_head or 'unavailable'}`",
                f"- next_tasks_file: `{next_tasks_path}`",
            ]
        )
        if rollback_recommended:
            plan_lines.append(
                f"- **롤백 권장**: 품질 하락이 감지되었습니다. `git reset --hard {git_head}` 검토 후 P1 항목만 수정하세요."
            )
        else:
            plan_lines.append("- 전략 변경이 필요하면 범위를 축소하고 P1 항목부터 안정화 작업을 우선 수행한다.")
        plan_lines.append("")
        return plan_lines
