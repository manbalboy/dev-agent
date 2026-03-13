"""Product-review stage runtime extraction for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage, utc_now_iso


class ProductReviewRuntime:
    """Encapsulate product-review scoring and artifact generation."""

    def __init__(
        self,
        *,
        set_stage: Callable[[str, JobStage, Path], None],
        docs_file: Callable[[Path, str], Path],
        read_text_file: Callable[[Path | None], str],
        read_json_file: Callable[[Path | None], Dict[str, Any]],
        extract_review_todo_items: Callable[[str], List[str]],
        collect_product_review_evidence,
        stable_issue_id: Callable[[str], str],
        build_operating_principle_alignment,
        summarize_operating_policy,
        build_repo_maturity_snapshot,
        build_quality_trend_snapshot,
        validate_product_review_payload,
        write_self_growing_effectiveness_artifact,
        fix_store,
    ) -> None:
        self.set_stage = set_stage
        self.docs_file = docs_file
        self.read_text_file = read_text_file
        self.read_json_file = read_json_file
        self.extract_review_todo_items = extract_review_todo_items
        self.collect_product_review_evidence_fn = collect_product_review_evidence
        self.stable_issue_id = stable_issue_id
        self.build_operating_principle_alignment_fn = build_operating_principle_alignment
        self.summarize_operating_policy_fn = summarize_operating_policy
        self.build_repo_maturity_snapshot_fn = build_repo_maturity_snapshot
        self.build_quality_trend_snapshot_fn = build_quality_trend_snapshot
        self.validate_product_review_payload_fn = validate_product_review_payload
        self.write_self_growing_effectiveness_artifact_fn = write_self_growing_effectiveness_artifact
        self.fix_store = fix_store

    def stage_product_review(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create PRODUCT_REVIEW.json and improvement backlog base."""

        self.set_stage(job.job_id, JobStage.PRODUCT_REVIEW, log_path)
        review_path = paths.get("review", self.docs_file(repository_path, "REVIEW.md"))
        review_text = self.read_text_file(review_path)
        todo_items = self.extract_review_todo_items(review_text)
        test_report_paths = sorted(repository_path.glob("TEST_REPORT_*.md"))
        test_failures = 0
        test_passes = 0
        for report in test_report_paths:
            text = self.read_text_file(report)
            if "Status: `PASS`" in text:
                test_passes += 1
            elif "Status: `FAIL`" in text:
                test_failures += 1

        architecture_exists = bool(self.read_text_file(paths.get("architecture_plan")))
        user_flows_exists = bool(self.read_text_file(paths.get("user_flows")))
        mvp_scope_exists = bool(self.read_text_file(paths.get("mvp_scope")))
        product_brief_exists = bool(self.read_text_file(paths.get("product_brief")))
        ux_review_text = self.read_text_file(self.docs_file(repository_path, "UX_REVIEW.md"))
        spec_text = self.read_text_file(paths.get("spec"))
        plan_text = self.read_text_file(paths.get("plan"))
        review_lower = review_text.lower()
        spec_lower = spec_text.lower()
        plan_lower = plan_text.lower()
        todo_penalty = min(3, len(todo_items) // 2)
        review_evidence = self.collect_product_review_evidence_fn(
            repository_path=repository_path,
            paths=paths,
            spec_text=spec_text,
            plan_text=plan_text,
            review_text=review_text,
            ux_review_text=ux_review_text,
            test_report_paths=test_report_paths,
            todo_items=todo_items,
        )
        source_summary = review_evidence.get("source_summary", {})
        state_signals = review_evidence.get("state_signals", {})
        artifact_health = review_evidence.get("artifact_health", {})
        source_todo_count = int(source_summary.get("todo_markers", 0) or 0)
        source_file_count = int(source_summary.get("source_file_count", 0) or 0)
        test_file_count = int(source_summary.get("test_file_count", 0) or 0)
        readme_exists = bool(source_summary.get("readme_exists"))
        runtime_manifest_count = int(source_summary.get("runtime_manifest_count", 0) or 0)
        error_source_hits = int(state_signals.get("error", {}).get("source_hits", 0) or 0)
        error_doc_hits = int(state_signals.get("error", {}).get("doc_hits", 0) or 0)
        empty_source_hits = int(state_signals.get("empty", {}).get("source_hits", 0) or 0)
        empty_doc_hits = int(state_signals.get("empty", {}).get("doc_hits", 0) or 0)
        loading_source_hits = int(state_signals.get("loading", {}).get("source_hits", 0) or 0)
        loading_doc_hits = int(state_signals.get("loading", {}).get("doc_hits", 0) or 0)

        critical_keywords = ["bug", "보안", "security", "crash", "크래시", "취약", "취약점"]
        has_critical = any(kw in review_lower for kw in critical_keywords)
        code_quality_score = max(
            1,
            5 - todo_penalty - (1 if has_critical else 0) - min(1, source_todo_count // 3),
        )
        code_quality_reason = (
            f"TODO {len(todo_items)}개"
            + (", 크리티컬 이슈(버그/보안/크래시) 감지" if has_critical else "")
            + f", 소스 TODO/FIXME {source_todo_count}개"
            + f", 소스 파일 {source_file_count}개"
        )

        arch_text = self.read_text_file(paths.get("architecture_plan")).lower()
        arch_has_layers = "layer" in arch_text or "레이어" in arch_text
        arch_has_gates = "quality gate" in arch_text or "품질 게이트" in arch_text
        arch_has_loop_safety = (
            "loop safety" in arch_text or "루프 안전" in arch_text or "loop_safety" in arch_text
        )
        arch_bonus = sum([arch_has_layers, arch_has_gates, arch_has_loop_safety])
        architecture_score = min(5, (3 if architecture_exists else 1) + (arch_bonus if architecture_exists else 0))
        architecture_reason = (
            f"ARCHITECTURE_PLAN {'있음' if architecture_exists else '없음'}"
            + (
                f", 레이어{'O' if arch_has_layers else 'X'}"
                f"/게이트{'O' if arch_has_gates else 'X'}"
                f"/루프안전{'O' if arch_has_loop_safety else 'X'}"
            )
        )

        mvp_text = self.read_text_file(paths.get("mvp_scope")).lower()
        mvp_has_out_of_scope = "out of scope" in mvp_text or "비범위" in mvp_text or "out_of_scope" in mvp_text
        mvp_has_gates = "acceptance gate" in mvp_text or "완료 조건" in mvp_text
        maintainability_score = (
            (3 if mvp_scope_exists else 1)
            + (1 if mvp_has_out_of_scope else 0)
            + (1 if product_brief_exists else 0)
            + (1 if readme_exists else 0)
            + (1 if runtime_manifest_count > 0 else 0)
        )
        maintainability_score = min(5, maintainability_score)
        maintainability_reason = (
            f"MVP_SCOPE {'있음' if mvp_scope_exists else '없음'}"
            + (
                f", 비범위정의{'O' if mvp_has_out_of_scope else 'X'}"
                f", PRODUCT_BRIEF{'O' if product_brief_exists else 'X'}"
                f", 완료조건{'O' if mvp_has_gates else 'X'}"
                f", README{'O' if readme_exists else 'X'}"
                f", 런타임매니페스트{'O' if runtime_manifest_count > 0 else 'X'}"
            )
        )

        flows_text = self.read_text_file(paths.get("user_flows")).lower()
        flows_has_primary = "primary flow" in flows_text or "primary" in flows_text
        flows_has_entry_exit = ("entry" in flows_text and "exit" in flows_text) or "진입" in flows_text
        usability_score = (
            (3 if user_flows_exists else 1)
            + (1 if flows_has_primary else 0)
            + (1 if flows_has_entry_exit else 0)
        )
        usability_score = min(5, usability_score)
        usability_reason = (
            f"USER_FLOWS {'있음' if user_flows_exists else '없음'}"
            + (f", primary flow{'O' if flows_has_primary else 'X'}" f", entry/exit{'O' if flows_has_entry_exit else 'X'}")
        )

        ux_lower = ux_review_text.lower()
        ux_no_failure = bool(ux_review_text) and ("실패/누락 없음" in ux_review_text or "all pass" in ux_lower)
        ux_has_state_check = "loading" in ux_lower or "empty" in ux_lower or "로딩" in ux_lower
        ux_clarity_score = ((2 if ux_review_text else 1) + (2 if ux_no_failure else 0) + (1 if ux_has_state_check else 0))
        ux_clarity_score = min(5, ux_clarity_score)
        ux_clarity_reason = (
            f"UX_REVIEW {'있음' if ux_review_text else '없음'}"
            + (f", 실패없음{'O' if ux_no_failure else 'X'}" f", 상태체크리스트{'O' if ux_has_state_check else 'X'}")
        )

        plan_has_test_strategy = (
            "test strategy" in plan_lower or "테스트 전략" in plan_lower or "test_strategy" in plan_lower
        )
        test_base = 3 if (test_report_paths or test_file_count > 0) else 1
        test_score = max(1, test_base - min(2, test_failures) + (1 if plan_has_test_strategy else 0))
        if test_file_count >= 2:
            test_score = min(5, test_score + 1)
        test_score = min(5, test_score)
        test_reason = (
            f"테스트 리포트 {len(test_report_paths)}개 (pass={test_passes}, fail={test_failures}), 테스트 파일 {test_file_count}개"
            + (", PLAN 테스트전략 있음" if plan_has_test_strategy else "")
        )

        def _state_score(
            keywords_spec: List[str],
            keywords_review: List[str],
            keywords_plan: List[str],
            *,
            source_hits: int,
            doc_hits: int,
        ) -> int:
            spec_signal = int(any(k in spec_lower for k in keywords_spec))
            review_signal = int(any(k in review_lower for k in keywords_review))
            plan_signal = int(any(k in plan_lower for k in keywords_plan))
            ui_signal = int(source_hits > 0)
            doc_signal = int(doc_hits > 0)
            return min(5, max(1, 1 + spec_signal + review_signal + plan_signal + ui_signal + doc_signal))

        error_score = _state_score(
            ["error", "오류", "에러", "exception"],
            ["오류", "error", "에러", "실패", "fail"],
            ["error handling", "에러 처리", "오류 처리"],
            source_hits=error_source_hits,
            doc_hits=error_doc_hits,
        )
        empty_score = _state_score(
            ["empty", "빈 상태", "데이터 없음"],
            ["빈 상태", "empty state", "empty"],
            ["empty state", "빈 상태 처리"],
            source_hits=empty_source_hits,
            doc_hits=empty_doc_hits,
        )
        loading_score = _state_score(
            ["loading", "로딩", "spinner"],
            ["로딩", "loading", "스피너"],
            ["loading state", "로딩 처리", "skeleton"],
            source_hits=loading_source_hits,
            doc_hits=loading_doc_hits,
        )

        scores = {
            "code_quality": code_quality_score,
            "architecture_structure": architecture_score,
            "maintainability": maintainability_score,
            "usability": usability_score,
            "ux_clarity": ux_clarity_score,
            "test_coverage": test_score,
            "error_state_handling": error_score,
            "empty_state_handling": empty_score,
            "loading_state_handling": loading_score,
        }
        overall = round(sum(scores.values()) / float(len(scores)), 2)

        score_reasons = {
            "code_quality": code_quality_reason,
            "architecture_structure": architecture_reason,
            "maintainability": maintainability_reason,
            "usability": usability_reason,
            "ux_clarity": ux_clarity_reason,
            "test_coverage": test_reason,
            "error_state_handling": f"에러 상태 점수: {error_score}/5 (source_hits={error_source_hits}, doc_hits={error_doc_hits})",
            "empty_state_handling": f"빈 상태 점수: {empty_score}/5 (source_hits={empty_source_hits}, doc_hits={empty_doc_hits})",
            "loading_state_handling": f"로딩 상태 점수: {loading_score}/5 (source_hits={loading_source_hits}, doc_hits={loading_doc_hits})",
        }
        category_evidence = {
            "code_quality": {
                "signals": ["review_todos", "critical_keywords", "source_todo_markers"],
                "metrics": {
                    "review_todo_count": len(todo_items),
                    "source_todo_markers": source_todo_count,
                    "critical_review_keywords": int(has_critical),
                    "source_file_count": source_file_count,
                },
            },
            "architecture_structure": {
                "signals": ["architecture_plan_sections"],
                "metrics": {
                    "architecture_plan_exists": int(architecture_exists),
                    "layer_section": int(arch_has_layers),
                    "quality_gate_section": int(arch_has_gates),
                    "loop_safety_section": int(arch_has_loop_safety),
                },
            },
            "maintainability": {
                "signals": ["mvp_scope_contract", "readme_presence", "runtime_manifest_presence"],
                "metrics": {
                    "mvp_scope_exists": int(mvp_scope_exists),
                    "out_of_scope_defined": int(mvp_has_out_of_scope),
                    "acceptance_gates_defined": int(mvp_has_gates),
                    "product_brief_exists": int(product_brief_exists),
                    "readme_exists": int(readme_exists),
                    "runtime_manifest_count": runtime_manifest_count,
                },
            },
            "usability": {
                "signals": ["user_flows_contract"],
                "metrics": {
                    "user_flows_exists": int(user_flows_exists),
                    "primary_flow_defined": int(flows_has_primary),
                    "entry_exit_defined": int(flows_has_entry_exit),
                },
            },
            "ux_clarity": {
                "signals": ["ux_review", "ux_state_checklist"],
                "metrics": {
                    "ux_review_exists": int(bool(ux_review_text)),
                    "ux_review_all_pass": int(ux_no_failure),
                    "ux_state_checklist": int(ux_has_state_check),
                },
            },
            "test_coverage": {
                "signals": ["test_reports", "test_files", "plan_test_strategy"],
                "metrics": {
                    "test_report_count": len(test_report_paths),
                    "test_passes_count": test_passes,
                    "test_failures_count": test_failures,
                    "test_file_count": test_file_count,
                    "plan_test_strategy": int(plan_has_test_strategy),
                },
            },
            "error_state_handling": state_signals.get("error", {}),
            "empty_state_handling": state_signals.get("empty", {}),
            "loading_state_handling": state_signals.get("loading", {}),
        }

        findings = [
            {
                "category": category,
                "score": scores[category],
                "max_score": 5,
                "summary": score_reasons[category],
                "action_needed": scores[category] <= 2,
                "evidence": category_evidence.get(category, {}),
            }
            for category in scores
        ]

        candidates: List[Dict[str, Any]] = []
        p1_keywords = ["bug", "fail", "error", "security", "crash", "보안", "크래시", "취약"]
        for item in todo_items:
            priority = "P1" if any(k in item.lower() for k in p1_keywords) else "P2"
            candidates.append(
                {
                    "id": self.stable_issue_id(item),
                    "source": "review_todo",
                    "title": item,
                    "priority": priority,
                    "reason": "REVIEW.md TODO 항목",
                    "action": "REVIEW.md의 해당 TODO를 해소하는 코드 수정",
                }
            )
        for category, score in scores.items():
            if score <= 2:
                action_map = {
                    "code_quality": "TODO 항목 해소 및 크리티컬 이슈 수정",
                    "architecture_structure": "ARCHITECTURE_PLAN.md에 레이어/게이트/루프안전 섹션 추가",
                    "maintainability": "MVP_SCOPE.md에 비범위 정의 및 완료 조건 보강",
                    "usability": "USER_FLOWS.md에 Primary Flow 및 진입/종료 조건 추가",
                    "ux_clarity": "UX_REVIEW.md 생성 또는 UX 상태 체크리스트 보강",
                    "test_coverage": "테스트 리포트 추가 및 PLAN에 테스트 전략 명시",
                    "error_state_handling": "에러 상태 UI 컴포넌트 및 메시지 구현",
                    "empty_state_handling": "빈 상태 UI 컴포넌트 및 안내 문구 구현",
                    "loading_state_handling": "로딩 스피너/스켈레톤 컴포넌트 구현",
                }
                candidates.append(
                    {
                        "id": self.stable_issue_id(category),
                        "source": "quality_score",
                        "title": f"{category} 점수 개선 (현재 {score}/5)",
                        "priority": "P1",
                        "reason": score_reasons[category],
                        "action": action_map.get(category, f"{category} 개선"),
                    }
                )
        dedup: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            dedup[item["id"]] = item
        ordered_candidates = sorted(
            dedup.values(),
            key=lambda item: (0 if item.get("priority") == "P1" else 1, str(item.get("title", ""))),
        )
        priority_summary = {
            "P0": sum(1 for item in ordered_candidates if item.get("priority") == "P0"),
            "P1": sum(1 for item in ordered_candidates if item.get("priority") == "P1"),
            "P2": sum(1 for item in ordered_candidates if item.get("priority") == "P2"),
            "P3": sum(1 for item in ordered_candidates if item.get("priority") == "P3"),
        }
        recommended_next_tasks = [
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
                "priority": str(item.get("priority", "P2")),
                "reason": str(item.get("reason", "")),
                "action": str(item.get("action", "")),
            }
            for item in ordered_candidates[:5]
        ]
        quality_signals = {
            "todo_items_count": len(todo_items),
            "critical_issue_keywords_detected": has_critical,
            "test_report_count": len(test_report_paths),
            "test_failures_count": test_failures,
            "test_passes_count": test_passes,
            "has_product_brief": product_brief_exists,
            "has_user_flows": user_flows_exists,
            "has_mvp_scope": mvp_scope_exists,
            "has_architecture_plan": architecture_exists,
            "has_ux_review": bool(ux_review_text),
        }
        evidence_summary = {
            "source_file_count": source_file_count,
            "test_file_count": test_file_count,
            "runtime_manifest_count": runtime_manifest_count,
            "readme_exists": readme_exists,
            "generated_doc_count": int(artifact_health.get("docs", {}).get("generated_count", 0) or 0),
            "state_signal_totals": {
                "error": error_source_hits + error_doc_hits,
                "empty": empty_source_hits + empty_doc_hits,
                "loading": loading_source_hits + loading_doc_hits,
            },
        }
        principle_alignment = self.build_operating_principle_alignment_fn(
            product_brief_exists=product_brief_exists,
            user_flows_exists=user_flows_exists,
            mvp_scope_exists=mvp_scope_exists,
            architecture_exists=architecture_exists,
            mvp_has_out_of_scope=mvp_has_out_of_scope,
            mvp_has_gates=mvp_has_gates,
            flows_has_primary=flows_has_primary,
            flows_has_entry_exit=flows_has_entry_exit,
            review_exists=bool(review_text),
            ux_review_exists=bool(ux_review_text),
            test_report_count=len(test_report_paths),
            todo_items_count=len(todo_items),
            priority_summary=priority_summary,
            candidate_count=len(ordered_candidates),
            scores=scores,
            overall=overall,
        )
        operating_policy = self.summarize_operating_policy_fn(principle_alignment)

        payload = {
            "schema_version": "1.1",
            "generated_at": utc_now_iso(),
            "job_id": job.job_id,
            "review_basis": {
                "spec": str(paths.get("spec", "")),
                "plan": str(paths.get("plan", "")),
                "review": str(review_path),
                "product_brief": str(paths.get("product_brief", "")),
                "user_flows": str(paths.get("user_flows", "")),
                "mvp_scope": str(paths.get("mvp_scope", "")),
                "architecture_plan": str(paths.get("architecture_plan", "")),
            },
            "scores": {**scores, "overall": overall},
            "score_reasons": score_reasons,
            "findings": findings,
            "improvement_candidates": ordered_candidates,
            "priority_summary": priority_summary,
            "recommended_next_tasks": recommended_next_tasks,
            "quality_signals": quality_signals,
            "artifact_health": artifact_health,
            "category_evidence": category_evidence,
            "evidence_summary": evidence_summary,
            "principle_alignment": principle_alignment,
            "operating_policy": operating_policy,
            "quality_gate": {
                "passed": overall >= 3.0,
                "threshold": 3.0,
                "reason": "overall >= 3.0 (1~5 척도, 각 카테고리 키워드+문서 존재 기반)",
                "categories_below_threshold": [category for category, score in scores.items() if score <= 2],
            },
        }
        validation = self.validate_product_review_payload_fn(payload)
        payload["validation"] = validation
        if not bool(validation.get("passed")):
            raise CommandExecutionError(
                "PRODUCT_REVIEW payload validation failed: "
                + "; ".join(str(item) for item in validation.get("errors", []))
            )
        product_review_path = paths.get("product_review", self.docs_file(repository_path, "PRODUCT_REVIEW.json"))
        product_review_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        review_history_path = paths.get("review_history", self.docs_file(repository_path, "REVIEW_HISTORY.json"))
        history_payload = self.read_json_file(review_history_path)
        history_entries = history_payload.get("entries", []) if isinstance(history_payload, dict) else []
        if not isinstance(history_entries, list):
            history_entries = []
        previous_level = ""
        if history_entries:
            previous_level = str(history_entries[-1].get("maturity_level", "")).strip()
        maturity_snapshot = self.build_repo_maturity_snapshot_fn(
            job_id=job.job_id,
            scores=scores,
            overall=overall,
            artifact_health=artifact_health,
            quality_gate=payload["quality_gate"],
            principle_alignment=principle_alignment,
            previous_level=previous_level,
        )
        history_entries.append(
            {
                "generated_at": payload["generated_at"],
                "job_id": job.job_id,
                "overall": overall,
                "scores": dict(scores),
                "maturity_level": maturity_snapshot["level"],
                "maturity_score": maturity_snapshot["score"],
                "top_issue_ids": [item["id"] for item in ordered_candidates[:3]],
            }
        )
        previous_overall = history_entries[-2]["overall"] if len(history_entries) >= 2 else overall
        score_delta = round(overall - previous_overall, 3)
        problem_text = "; ".join(item.get("id", "") for item in ordered_candidates[:3])
        diff_text = str(priority_summary or findings or "")[:800]
        self.fix_store.upsert(
            job_id=job.job_id,
            problem=problem_text,
            diff_summary=diff_text,
            score_delta=score_delta,
        )
        review_history_path.write_text(
            json.dumps({"entries": history_entries[-30:]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        trend_snapshot = self.build_quality_trend_snapshot_fn(
            job_id=job.job_id,
            history_entries=history_entries[-30:],
            maturity_snapshot=maturity_snapshot,
        )
        repo_maturity_path = paths.get("repo_maturity", self.docs_file(repository_path, "REPO_MATURITY.json"))
        repo_maturity_path.write_text(
            json.dumps(maturity_snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        quality_trend_path = paths.get("quality_trend", self.docs_file(repository_path, "QUALITY_TREND.json"))
        quality_trend_path.write_text(
            json.dumps(trend_snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        backlog_path = paths.get("improvement_backlog", self.docs_file(repository_path, "IMPROVEMENT_BACKLOG.json"))
        backlog_path.write_text(
            json.dumps(
                {
                    "generated_at": payload["generated_at"],
                    "source_review": str(product_review_path),
                    "items": ordered_candidates,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_self_growing_effectiveness_artifact_fn(
            job=job,
            repository_path=repository_path,
            paths=paths,
            review_payload=payload,
            maturity_snapshot=maturity_snapshot,
            trend_snapshot=trend_snapshot,
            review_history_entries=history_entries[-30:],
        )

    def collect_product_review_evidence(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
        spec_text: str,
        plan_text: str,
        review_text: str,
        ux_review_text: str,
        test_report_paths: List[Path],
        todo_items: List[str],
    ) -> Dict[str, Any]:
        """Collect evidence-backed signals for PRODUCT_REVIEW scoring."""

        excluded_dirs = {
            ".git",
            "_docs",
            "node_modules",
            ".next",
            "dist",
            "build",
            ".venv",
            "venv",
            "__pycache__",
            ".pytest_cache",
            "coverage",
        }
        source_exts = {
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".vue",
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".json",
            ".md",
        }
        source_paths: List[Path] = []
        test_paths: List[Path] = []
        manifest_names = {"package.json", "pyproject.toml", "requirements.txt", "deno.json", "Cargo.toml"}
        runtime_manifest_count = 0

        for path in repository_path.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(repository_path)
            if any(part in excluded_dirs for part in relative.parts):
                continue
            if path.name in manifest_names:
                runtime_manifest_count += 1
            if path.suffix.lower() in source_exts:
                source_paths.append(path)
                lowered_name = path.name.lower()
                lowered_parts = "/".join(part.lower() for part in relative.parts)
                if (
                    lowered_name.startswith("test_")
                    or lowered_name.endswith(".test.ts")
                    or lowered_name.endswith(".test.tsx")
                    or lowered_name.endswith(".spec.ts")
                    or lowered_name.endswith(".spec.tsx")
                    or lowered_name.endswith(".test.js")
                    or lowered_name.endswith(".spec.js")
                    or "/tests/" in f"/{lowered_parts}/"
                ):
                    test_paths.append(path)

        def _read_limited_text(file_path: Path, *, max_chars: int = 64000) -> str:
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    return handle.read(max_chars).lower()
            except OSError:
                return ""

        def _is_ui_layer_file(file_path: Path) -> bool:
            relative = file_path.relative_to(repository_path)
            lowered_parts = [part.lower() for part in relative.parts]
            lowered_name = file_path.name.lower()
            if file_path.suffix.lower() in {".tsx", ".jsx", ".vue", ".html"}:
                return True
            ui_dirs = {"components", "component", "pages", "views", "screens", "templates", "ui", "widgets"}
            if any(part in ui_dirs for part in lowered_parts):
                return file_path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx", ".vue", ".html"}
            return lowered_name.endswith((".page.tsx", ".screen.tsx", ".view.tsx"))

        source_todo_markers = 0
        analyzed_source_file_count = 0
        analyzed_ui_file_count = 0
        state_source_hits = {"error": 0, "empty": 0, "loading": 0}
        state_source_keywords = {
            "error": ["error", "failed", "retry", "alert", "toast", "fallback"],
            "empty": ["empty", "no data", "no results", "placeholder", "not found", "데이터 없음"],
            "loading": ["loading", "spinner", "skeleton", "pending", "isloading", "aria-busy"],
        }
        for file_path in source_paths[:400]:
            text = _read_limited_text(file_path)
            if not text:
                continue
            analyzed_source_file_count += 1
            source_todo_markers += sum(text.count(marker) for marker in ["todo", "fixme", "hack"])
            if not _is_ui_layer_file(file_path):
                continue
            analyzed_ui_file_count += 1
            for state_name, keywords in state_source_keywords.items():
                if any(keyword in text for keyword in keywords):
                    state_source_hits[state_name] += 1

        user_flows_text = self.read_text_file(paths.get("user_flows")).lower()
        state_doc_sources = [user_flows_text, ux_review_text.lower()]

        generated_docs = {
            "product_brief": bool(self.read_text_file(paths.get("product_brief"))),
            "user_flows": bool(self.read_text_file(paths.get("user_flows"))),
            "mvp_scope": bool(self.read_text_file(paths.get("mvp_scope"))),
            "architecture_plan": bool(self.read_text_file(paths.get("architecture_plan"))),
            "scaffold_plan": bool(self.read_text_file(paths.get("scaffold_plan"))),
            "review": bool(review_text),
            "ux_review": bool(ux_review_text),
            "test_reports": len(test_report_paths),
        }

        def _state_signal_payload(name: str, doc_keywords: List[str]) -> Dict[str, Any]:
            source_hits = int(state_source_hits.get(name, 0) or 0)
            doc_hits = sum(
                1
                for source_text in state_doc_sources
                if source_text and any(keyword in source_text for keyword in doc_keywords)
            )
            return {
                "signals": ["ui_file_presence", "document_presence"],
                "metrics": {
                    "ui_candidate_file_count": analyzed_ui_file_count,
                    "source_hits": source_hits,
                    "doc_hits": doc_hits,
                    "keywords": state_source_keywords.get(name, []),
                },
                "source_hits": source_hits,
                "doc_hits": doc_hits,
            }

        return {
            "source_summary": {
                "source_file_count": len(source_paths),
                "test_file_count": len(test_paths),
                "analyzed_source_file_count": analyzed_source_file_count,
                "analyzed_ui_file_count": analyzed_ui_file_count,
                "runtime_manifest_count": runtime_manifest_count,
                "readme_exists": (repository_path / "README.md").exists(),
                "todo_markers": source_todo_markers,
                "review_todo_count": len(todo_items),
            },
            "artifact_health": {
                "docs": {
                    **generated_docs,
                    "generated_count": sum(
                        int(value) if isinstance(value, bool) else (1 if value else 0)
                        for value in generated_docs.values()
                    ),
                },
                "repo": {
                    "source_file_count": len(source_paths),
                    "test_file_count": len(test_paths),
                    "runtime_manifest_count": runtime_manifest_count,
                    "readme_exists": (repository_path / "README.md").exists(),
                },
                "tests": {
                    "report_count": len(test_report_paths),
                    "test_file_count": len(test_paths),
                },
            },
            "state_signals": {
                "error": _state_signal_payload("error", ["error", "오류", "에러", "실패"]),
                "empty": _state_signal_payload("empty", ["empty", "빈 상태", "데이터 없음"]),
                "loading": _state_signal_payload("loading", ["loading", "로딩", "spinner", "skeleton"]),
            },
        }

    @staticmethod
    def build_operating_principle_alignment(
        *,
        product_brief_exists: bool,
        user_flows_exists: bool,
        mvp_scope_exists: bool,
        architecture_exists: bool,
        mvp_has_out_of_scope: bool,
        mvp_has_gates: bool,
        flows_has_primary: bool,
        flows_has_entry_exit: bool,
        review_exists: bool,
        ux_review_exists: bool,
        test_report_count: int,
        todo_items_count: int,
        priority_summary: Dict[str, int],
        candidate_count: int,
        scores: Dict[str, int],
        overall: float,
    ) -> Dict[str, Dict[str, Any]]:
        """Evaluate top-level operating principles with explicit evidence."""

        alignment: Dict[str, Dict[str, Any]] = {}

        def add(
            principle_id: str,
            title: str,
            status: str,
            summary: str,
            evidence: List[str],
            enforced_by: str,
        ) -> None:
            alignment[principle_id] = {
                "title": title,
                "status": status,
                "summary": summary,
                "evidence": evidence,
                "enforced_by": enforced_by,
            }

        add(
            "principle_1_mvp_first",
            "MVP 우선 원칙",
            "aligned" if (mvp_scope_exists and mvp_has_out_of_scope and mvp_has_gates) else "blocked",
            "MVP 범위와 완료 게이트가 문서로 고정되어야 구현이 안정된다.",
            [
                f"MVP_SCOPE={'O' if mvp_scope_exists else 'X'}",
                f"OutOfScope={'O' if mvp_has_out_of_scope else 'X'}",
                f"AcceptanceGates={'O' if mvp_has_gates else 'X'}",
            ],
            "MVP_SCOPE.md + implementation hard gate",
        )
        add(
            "principle_2_design_first",
            "설계 선행 원칙",
            "aligned" if all([product_brief_exists, user_flows_exists, mvp_scope_exists, architecture_exists]) else "blocked",
            "제품 정의와 설계 문서가 구현보다 먼저 준비되어야 한다.",
            [
                f"PRODUCT_BRIEF={'O' if product_brief_exists else 'X'}",
                f"USER_FLOWS={'O' if user_flows_exists else 'X'}",
                f"MVP_SCOPE={'O' if mvp_scope_exists else 'X'}",
                f"ARCHITECTURE_PLAN={'O' if architecture_exists else 'X'}",
            ],
            "product-definition hard gate",
        )
        add(
            "principle_3_small_batch",
            "작은 단위 개발 원칙",
            "aligned"
            if priority_summary.get("P1", 0) <= 3 and candidate_count <= 8
            else "warning",
            "한 라운드의 우선 개선 항목이 과도하게 많으면 범위 축소가 필요하다.",
            [
                f"P1={priority_summary.get('P1', 0)}",
                f"candidate_count={candidate_count}",
                f"todo_items={todo_items_count}",
            ],
            "improvement backlog prioritization",
        )
        add(
            "principle_4_evaluation_first",
            "평가 우선 원칙",
            "aligned" if (review_exists and (test_report_count > 0 or ux_review_exists)) else "warning",
            "리뷰, 테스트, UX 근거가 있어야 생성보다 평가를 우선할 수 있다.",
            [
                f"REVIEW={'O' if review_exists else 'X'}",
                f"TEST_REPORTS={test_report_count}",
                f"UX_REVIEW={'O' if ux_review_exists else 'X'}",
            ],
            "REVIEW.md + TEST_REPORT + UX_REVIEW",
        )
        add(
            "principle_5_iterative_improvement",
            "반복 개선 원칙",
            "aligned",
            "리뷰 결과를 backlog와 next tasks로 변환해 다음 라운드 입력으로 사용한다.",
            [f"candidate_count={candidate_count}"],
            "PRODUCT_REVIEW -> IMPROVEMENT_BACKLOG -> NEXT_IMPROVEMENT_TASKS",
        )
        add(
            "principle_6_no_repeat_same_fix",
            "반복 오류 금지 원칙",
            "runtime",
            "같은 문제 반복 여부는 improvement_stage에서 히스토리 기반으로 판단한다.",
            ["repeat-limit/stagnation/regression signals handled at runtime"],
            "improvement_stage loop guard",
        )
        product_quality_ok = all(
            scores.get(key, 0) >= 3
            for key in [
                "usability",
                "ux_clarity",
                "error_state_handling",
                "empty_state_handling",
                "loading_state_handling",
            ]
        )
        add(
            "principle_7_product_quality_bar",
            "제품 품질 기준 원칙",
            "aligned" if (overall >= 3.0 and product_quality_ok and flows_has_primary and flows_has_entry_exit) else "warning",
            "기능 동작뿐 아니라 사용 흐름, UX 명확성, 상태 처리가 함께 충족되어야 한다.",
            [
                f"overall={overall}",
                f"usability={scores.get('usability', 0)}",
                f"ux_clarity={scores.get('ux_clarity', 0)}",
                f"error={scores.get('error_state_handling', 0)}",
                f"empty={scores.get('empty_state_handling', 0)}",
                f"loading={scores.get('loading_state_handling', 0)}",
            ],
            "product_review score gate",
        )
        add(
            "principle_8_record_decisions",
            "기록 원칙",
            "aligned" if all([product_brief_exists, user_flows_exists, mvp_scope_exists, architecture_exists, review_exists]) else "warning",
            "제품 정의와 리뷰 문서가 남아 있어야 이후 개선이 설명 가능하다.",
            [
                f"PRODUCT_BRIEF={'O' if product_brief_exists else 'X'}",
                f"USER_FLOWS={'O' if user_flows_exists else 'X'}",
                f"MVP_SCOPE={'O' if mvp_scope_exists else 'X'}",
                f"ARCHITECTURE_PLAN={'O' if architecture_exists else 'X'}",
                f"REVIEW={'O' if review_exists else 'X'}",
            ],
            "_docs artifact set",
        )
        add(
            "principle_9_stability_protection",
            "안정성 보호 원칙",
            "aligned" if (test_report_count > 0 and architecture_exists and mvp_has_gates) else "warning",
            "테스트와 품질 게이트가 있어야 품질 하락을 방지할 수 있다.",
            [
                f"test_report_count={test_report_count}",
                f"ARCHITECTURE_PLAN={'O' if architecture_exists else 'X'}",
                f"MVP_gates={'O' if mvp_has_gates else 'X'}",
            ],
            "test gate + architecture quality gate",
        )
        add(
            "principle_10_continuous_evolution",
            "지속 진화 원칙",
            "aligned",
            "생성 후 종료하지 않고 review/history/backlog를 통해 다음 개선 루프로 연결한다.",
            [
                f"candidate_count={candidate_count}",
                "review history is appended every round",
            ],
            "review_history + improvement_stage",
        )

        return alignment

    @staticmethod
    def summarize_operating_policy(
        principle_alignment: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Summarize which operating principles require action."""

        blocked = [key for key, value in principle_alignment.items() if str(value.get("status", "")) == "blocked"]
        warnings = [key for key, value in principle_alignment.items() if str(value.get("status", "")) == "warning"]
        runtime = [key for key, value in principle_alignment.items() if str(value.get("status", "")) == "runtime"]
        return {
            "blocked_principles": blocked,
            "warning_principles": warnings,
            "runtime_principles": runtime,
            "requires_design_reset": "principle_2_design_first" in blocked,
            "requires_scope_reset": (
                "principle_1_mvp_first" in blocked or "principle_3_small_batch" in warnings
            ),
            "requires_quality_focus": (
                "principle_4_evaluation_first" in warnings
                or "principle_7_product_quality_bar" in warnings
                or "principle_9_stability_protection" in warnings
            ),
        }

    @staticmethod
    def build_repo_maturity_snapshot(
        *,
        job_id: str,
        scores: Dict[str, int],
        overall: float,
        artifact_health: Dict[str, Any],
        quality_gate: Dict[str, Any],
        principle_alignment: Dict[str, Dict[str, Any]],
        previous_level: str,
    ) -> Dict[str, Any]:
        """Derive one repo maturity snapshot from review evidence."""

        docs_info = artifact_health.get("docs", {}) if isinstance(artifact_health, dict) else {}
        repo_info = artifact_health.get("repo", {}) if isinstance(artifact_health, dict) else {}
        tests_info = artifact_health.get("tests", {}) if isinstance(artifact_health, dict) else {}

        docs_generated = int(docs_info.get("generated_count", 0) or 0)
        source_file_count = int(repo_info.get("source_file_count", 0) or 0)
        test_file_count = int(tests_info.get("test_file_count", 0) or 0)
        test_report_count = int(tests_info.get("report_count", 0) or 0)
        quality_gate_passed = bool(quality_gate.get("passed"))
        blocked_principles = sum(
            1
            for item in principle_alignment.values()
            if isinstance(item, dict) and str(item.get("status", "")) == "blocked"
        )
        categories_below = quality_gate.get("categories_below_threshold", [])
        if not isinstance(categories_below, list):
            categories_below = []

        score_all_ge_4 = all(int(value or 0) >= 4 for value in scores.values())
        score_product_ok = all(
            int(scores.get(key, 0) or 0) >= 3
            for key in [
                "usability",
                "ux_clarity",
                "error_state_handling",
                "empty_state_handling",
                "loading_state_handling",
            ]
        )

        level = "bootstrap"
        if overall >= 2.4 and docs_generated >= 4 and source_file_count >= 1:
            level = "mvp"
        if (
            overall >= 3.0
            and quality_gate_passed
            and docs_generated >= 6
            and test_file_count >= 1
            and score_product_ok
        ):
            level = "usable"
        if (
            overall >= 3.7
            and quality_gate_passed
            and docs_generated >= 7
            and test_file_count >= 2
            and test_report_count >= 1
            and score_product_ok
            and blocked_principles == 0
            and len(categories_below) == 0
        ):
            level = "stable"
        if (
            overall >= 4.4
            and quality_gate_passed
            and docs_generated >= 7
            and test_file_count >= 2
            and test_report_count >= 1
            and blocked_principles == 0
            and len(categories_below) == 0
            and score_all_ge_4
        ):
            level = "product_grade"

        level_order = ["bootstrap", "mvp", "usable", "stable", "product_grade"]
        level_rank = {name: idx for idx, name in enumerate(level_order)}
        previous_rank = level_rank.get(previous_level or "bootstrap", 0)
        current_rank = level_rank.get(level, 0)
        progression = "unchanged"
        if current_rank > previous_rank:
            progression = "up"
        elif current_rank < previous_rank:
            progression = "down"

        docs_ratio = min(1.0, docs_generated / 8.0)
        tests_ratio = min(1.0, (test_file_count + test_report_count) / 4.0)
        penalty = min(12, blocked_principles * 4 + len(categories_below) * 2)
        maturity_score = int(
            round(
                min(
                    100.0,
                    max(
                        0.0,
                        (overall / 5.0) * 65.0 + docs_ratio * 20.0 + tests_ratio * 15.0 - penalty,
                    ),
                )
            )
        )

        return {
            "generated_at": utc_now_iso(),
            "job_id": job_id,
            "level": level,
            "score": maturity_score,
            "previous_level": previous_level or "",
            "progression": progression,
            "quality_gate_passed": quality_gate_passed,
            "evidence": {
                "overall": overall,
                "source_file_count": source_file_count,
                "generated_doc_count": docs_generated,
                "test_file_count": test_file_count,
                "test_report_count": test_report_count,
                "blocked_principles": blocked_principles,
                "categories_below_threshold": len(categories_below),
            },
        }

    @staticmethod
    def build_quality_trend_snapshot(
        *,
        job_id: str,
        history_entries: List[Dict[str, Any]],
        maturity_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Summarize quality movement across recent review history."""

        current_overall = float(history_entries[-1].get("overall", 0.0)) if history_entries else 0.0
        previous_overall = float(history_entries[-2].get("overall", 0.0)) if len(history_entries) >= 2 else 0.0
        delta_from_previous = round(current_overall - previous_overall, 2) if len(history_entries) >= 2 else 0.0
        recent_scores = [
            float(item.get("overall", 0.0)) for item in history_entries[-5:] if item.get("overall") is not None
        ]
        rolling_average_3 = (
            round(
                sum(float(item.get("overall", 0.0)) for item in history_entries[-3:])
                / max(1, len(history_entries[-3:])),
                2,
            )
            if history_entries
            else 0.0
        )
        best_overall = round(max(recent_scores), 2) if recent_scores else current_overall
        worst_overall = round(min(recent_scores), 2) if recent_scores else current_overall
        trend_direction = "stable"
        if delta_from_previous >= 0.2:
            trend_direction = "improving"
        elif delta_from_previous <= -0.2:
            trend_direction = "declining"

        improving_streak = 0
        for older, newer in zip(history_entries[:-1], history_entries[1:]):
            if float(newer.get("overall", 0.0)) > float(older.get("overall", 0.0)):
                improving_streak += 1

        tracked_categories = [
            "code_quality",
            "architecture_structure",
            "maintainability",
            "usability",
            "ux_clarity",
            "test_coverage",
            "error_state_handling",
            "empty_state_handling",
            "loading_state_handling",
        ]
        category_latest_scores: Dict[str, int] = {}
        category_deltas: Dict[str, int] = {}
        category_trend_direction: Dict[str, str] = {}
        persistent_low_categories: List[str] = []
        stagnant_categories: List[str] = []
        declining_categories: List[str] = []

        for category in tracked_categories:
            category_history: List[int] = []
            for entry in history_entries:
                scores_payload = entry.get("scores", {})
                if not isinstance(scores_payload, dict):
                    continue
                value = scores_payload.get(category)
                if value is None:
                    continue
                try:
                    category_history.append(int(value))
                except (TypeError, ValueError):
                    continue

            if not category_history:
                continue

            category_latest_scores[category] = int(category_history[-1])
            if len(category_history) >= 2:
                delta = int(category_history[-1]) - int(category_history[-2])
                category_deltas[category] = delta
                if delta > 0:
                    category_trend_direction[category] = "improving"
                elif delta < 0:
                    category_trend_direction[category] = "declining"
                    declining_categories.append(category)
                else:
                    category_trend_direction[category] = "stable"
            else:
                category_trend_direction[category] = "stable"

            recent_window = category_history[-3:]
            if len(recent_window) >= 3 and all(score <= 2 for score in recent_window):
                persistent_low_categories.append(category)
            if len(recent_window) >= 3 and max(recent_window) == min(recent_window):
                stagnant_categories.append(category)

        return {
            "generated_at": utc_now_iso(),
            "job_id": job_id,
            "review_round_count": len(history_entries),
            "current_overall": current_overall,
            "previous_overall": previous_overall if len(history_entries) >= 2 else None,
            "delta_from_previous": delta_from_previous if len(history_entries) >= 2 else None,
            "rolling_average_3": rolling_average_3,
            "best_overall": best_overall,
            "worst_overall": worst_overall,
            "trend_direction": trend_direction,
            "score_stagnation_detected": len(recent_scores) >= 3 and (max(recent_scores) - min(recent_scores) <= 0.15),
            "quality_regression_detected": delta_from_previous <= -0.2 if len(history_entries) >= 2 else False,
            "maturity_level": str(maturity_snapshot.get("level", "")).strip(),
            "previous_maturity_level": str(maturity_snapshot.get("previous_level", "")).strip(),
            "maturity_progression": str(maturity_snapshot.get("progression", "unchanged")).strip(),
            "improving_streak": improving_streak,
            "category_latest_scores": category_latest_scores,
            "category_deltas": category_deltas,
            "category_trend_direction": category_trend_direction,
            "persistent_low_categories": persistent_low_categories,
            "stagnant_categories": stagnant_categories,
            "declining_categories": declining_categories,
        }

    @staticmethod
    def validate_product_review_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate PRODUCT_REVIEW payload at runtime without external schema libs."""

        required_scores = [
            "code_quality",
            "architecture_structure",
            "maintainability",
            "usability",
            "ux_clarity",
            "test_coverage",
            "error_state_handling",
            "empty_state_handling",
            "loading_state_handling",
            "overall",
        ]
        errors: List[str] = []
        if not isinstance(payload, dict):
            return {"passed": False, "errors": ["payload must be object"]}
        scores = payload.get("scores")
        if not isinstance(scores, dict):
            errors.append("scores must be object")
            scores = {}
        for key in required_scores:
            value = scores.get(key)
            if not isinstance(value, (int, float)):
                errors.append(f"scores.{key} must be number")
                continue
            if value < 0 or value > 5:
                errors.append(f"scores.{key} out of range (0..5): {value}")
        findings = payload.get("findings")
        if not isinstance(findings, list) or not findings:
            errors.append("findings must be non-empty array")
        candidates = payload.get("improvement_candidates")
        if not isinstance(candidates, list):
            errors.append("improvement_candidates must be array")
        artifact_health = payload.get("artifact_health")
        if artifact_health is not None and not isinstance(artifact_health, dict):
            errors.append("artifact_health must be object when present")
        category_evidence = payload.get("category_evidence")
        if category_evidence is not None and not isinstance(category_evidence, dict):
            errors.append("category_evidence must be object when present")
        evidence_summary = payload.get("evidence_summary")
        if evidence_summary is not None and not isinstance(evidence_summary, dict):
            errors.append("evidence_summary must be object when present")
        principle_alignment = payload.get("principle_alignment")
        if not isinstance(principle_alignment, dict) or not principle_alignment:
            errors.append("principle_alignment must be non-empty object")
        operating_policy = payload.get("operating_policy")
        if not isinstance(operating_policy, dict):
            errors.append("operating_policy must be object")
        gate = payload.get("quality_gate")
        if not isinstance(gate, dict) or "passed" not in gate:
            errors.append("quality_gate.passed is required")
        return {
            "passed": not errors,
            "errors": errors,
            "checked_at": utc_now_iso(),
        }
