"""Structured memory/convention helper extraction for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.models import JobRecord, utc_now_iso


class StructuredMemoryRuntime:
    """Encapsulate structured memory artifacts, failure patterns, and conventions."""

    def __init__(
        self,
        *,
        feature_enabled: Callable[[str], bool],
        docs_file: Callable[[Path, str], Path],
        job_execution_repository: Callable[[JobRecord], str],
        upsert_jsonl_entries,
        upsert_json_history_entries,
        write_json_artifact: Callable[[Optional[Path], Dict[str, Any]], None],
        write_memory_quality_artifacts,
        read_json_file: Callable[[Path | None], Dict[str, Any]],
        read_text_file: Callable[[Optional[Path]], str],
    ) -> None:
        self.feature_enabled = feature_enabled
        self.docs_file = docs_file
        self.job_execution_repository = job_execution_repository
        self.upsert_jsonl_entries = upsert_jsonl_entries
        self.upsert_json_history_entries = upsert_json_history_entries
        self.write_json_artifact = write_json_artifact
        self.write_memory_quality_artifacts = write_memory_quality_artifacts
        self.read_json_file = read_json_file
        self.read_text_file = read_text_file

    def write_structured_memory_artifacts(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        review_payload: Dict[str, Any],
        maturity_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
        next_tasks_payload: Dict[str, Any],
    ) -> None:
        """Write structured memory artifacts from review/improvement outputs."""

        memory_logging_enabled = self.feature_enabled("memory_logging")
        convention_extraction_enabled = self.feature_enabled("convention_extraction")
        memory_scoring_enabled = self.feature_enabled("memory_scoring")
        generated_at = str(loop_state.get("generated_at", "")).strip() or utc_now_iso()
        scores = review_payload.get("scores", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(scores, dict):
            scores = {}
        overall = float(scores.get("overall", 0.0) or 0.0)
        recommended_tasks = next_tasks_payload.get("tasks", []) if isinstance(next_tasks_payload, dict) else []
        if not isinstance(recommended_tasks, list):
            recommended_tasks = []
        categories_below = loop_state.get("categories_below_threshold", []) if isinstance(loop_state, dict) else []
        if not isinstance(categories_below, list):
            categories_below = []

        memory_log_path = paths.get("memory_log", self.docs_file(repository_path, "MEMORY_LOG.jsonl"))
        decision_history_path = paths.get("decision_history", self.docs_file(repository_path, "DECISION_HISTORY.json"))
        failure_patterns_path = paths.get("failure_patterns", self.docs_file(repository_path, "FAILURE_PATTERNS.json"))
        conventions_path = paths.get("conventions", self.docs_file(repository_path, "CONVENTIONS.json"))
        memory_feedback_path = paths.get("memory_feedback", self.docs_file(repository_path, "MEMORY_FEEDBACK.json"))
        memory_rankings_path = paths.get("memory_rankings", self.docs_file(repository_path, "MEMORY_RANKINGS.json"))

        base_payload = {
            "job_id": job.job_id,
            "app_code": job.app_code,
            "repository": job.repository,
            "execution_repository": self.job_execution_repository(job),
            "workflow_id": str(job.workflow_id or "").strip(),
            "issue_number": int(job.issue_number or 0),
            "issue_title": str(job.issue_title or "").strip(),
            "issue_url": str(job.issue_url or "").strip(),
            "generated_at": generated_at,
        }

        episodic_entry = {
            "memory_id": f"episodic_job_summary:{job.job_id}",
            "memory_type": "episodic",
            **base_payload,
            "signals": {
                "strategy": str(loop_state.get("strategy", "")).strip(),
                "strategy_focus": str(loop_state.get("strategy_focus", "")).strip(),
                "scope_restriction": str(loop_state.get("next_scope_restriction", "")).strip(),
                "overall": overall,
                "quality_trend_direction": str(trend_payload.get("trend_direction", "")).strip(),
                "delta_from_previous": float(trend_payload.get("delta_from_previous", 0.0) or 0.0),
                "maturity_level": str(maturity_payload.get("level", "")).strip(),
                "maturity_progression": str(maturity_payload.get("progression", "")).strip(),
                "persistent_low_categories": list(trend_payload.get("persistent_low_categories", []) or []),
                "stagnant_categories": list(trend_payload.get("stagnant_categories", []) or []),
                "categories_below_threshold": categories_below,
                "recovery_mode": "resume"
                if str(loop_state.get("next_scope_restriction", "")).strip() != "normal"
                else "normal",
            },
            "artifacts": {
                "product_review": str(paths.get("product_review", Path("_docs/PRODUCT_REVIEW.json"))),
                "review_history": str(paths.get("review_history", Path("_docs/REVIEW_HISTORY.json"))),
                "repo_maturity": str(paths.get("repo_maturity", Path("_docs/REPO_MATURITY.json"))),
                "quality_trend": str(paths.get("quality_trend", Path("_docs/QUALITY_TREND.json"))),
                "improvement_loop_state": str(paths.get("improvement_loop_state", Path("_docs/IMPROVEMENT_LOOP_STATE.json"))),
                "next_improvement_tasks": str(paths.get("next_improvement_tasks", Path("_docs/NEXT_IMPROVEMENT_TASKS.json"))),
            },
            "outcome": {
                "quality_gate_passed": bool(review_payload.get("quality_gate", {}).get("passed", False)),
                "task_count": len(recommended_tasks),
                "recommended_task_titles": [
                    str(item.get("title", "")).strip()
                    for item in recommended_tasks[:5]
                    if isinstance(item, dict) and str(item.get("title", "")).strip()
                ],
            },
        }
        decision_entry = {
            "decision_id": f"improvement_strategy:{job.job_id}",
            **base_payload,
            "decision_type": "improvement_strategy",
            "chosen_strategy": str(loop_state.get("strategy", "")).strip(),
            "strategy_focus": str(loop_state.get("strategy_focus", "")).strip(),
            "scope_restriction": str(loop_state.get("next_scope_restriction", "")).strip(),
            "trigger_signals": dict(loop_state.get("strategy_inputs", {}) or {}),
            "change_reasons": list(loop_state.get("strategy_change_reasons", []) or []),
            "selected_task_ids": [
                str(item.get("source_issue_id", "")).strip()
                for item in recommended_tasks
                if isinstance(item, dict) and str(item.get("source_issue_id", "")).strip()
            ],
            "selected_task_titles": [
                str(item.get("title", "")).strip()
                for item in recommended_tasks
                if isinstance(item, dict) and str(item.get("title", "")).strip()
            ],
        }

        if memory_logging_enabled:
            self.upsert_jsonl_entries(memory_log_path, [episodic_entry], key_field="memory_id")
            self.upsert_json_history_entries(
                decision_history_path,
                [decision_entry],
                key_field="decision_id",
                root_key="entries",
                max_entries=200,
            )
            self.update_failure_patterns_artifact(
                failure_patterns_path=failure_patterns_path,
                review_payload=review_payload,
                loop_state=loop_state,
                trend_payload=trend_payload,
                next_tasks_payload=next_tasks_payload,
                generated_at=generated_at,
            )
        if convention_extraction_enabled:
            self.write_conventions_artifact(
                repository_path=repository_path,
                conventions_path=conventions_path,
                job=job,
                generated_at=generated_at,
            )
        else:
            self.write_json_artifact(
                conventions_path,
                {"generated_at": generated_at, "enabled": False, "rules": []},
            )
        if memory_scoring_enabled:
            self.write_memory_quality_artifacts(
                job=job,
                paths=paths,
                review_payload=review_payload,
                trend_payload=trend_payload,
                loop_state=loop_state,
                generated_at=generated_at,
                current_memory_ids=[episodic_entry["memory_id"], decision_entry["decision_id"]],
                memory_feedback_path=memory_feedback_path,
                memory_rankings_path=memory_rankings_path,
            )
        else:
            self.write_json_artifact(
                memory_feedback_path,
                {"generated_at": generated_at, "enabled": False, "entries": []},
            )
            self.write_json_artifact(
                memory_rankings_path,
                {"generated_at": generated_at, "enabled": False, "items": []},
            )

    def update_failure_patterns_artifact(
        self,
        *,
        failure_patterns_path: Path,
        review_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
        trend_payload: Dict[str, Any],
        next_tasks_payload: Dict[str, Any],
        generated_at: str,
    ) -> None:
        """Accumulate recurring failure/quality patterns in one structured file."""

        existing_payload = self.read_json_file(failure_patterns_path)
        current_items = existing_payload.get("items", []) if isinstance(existing_payload, dict) else []
        if not isinstance(current_items, list):
            current_items = []
        merged: Dict[str, Dict[str, Any]] = {}
        for item in current_items:
            if not isinstance(item, dict):
                continue
            pattern_id = str(item.get("pattern_id", "")).strip()
            if pattern_id:
                merged[pattern_id] = item

        categories_below = review_payload.get("quality_gate", {}).get("categories_below_threshold", [])
        if not isinstance(categories_below, list):
            categories_below = []
        persistent_low = trend_payload.get("persistent_low_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(persistent_low, list):
            persistent_low = []
        stagnant = trend_payload.get("stagnant_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(stagnant, list):
            stagnant = []
        next_titles = [
            str(item.get("title", "")).strip()
            for item in (next_tasks_payload.get("tasks", []) if isinstance(next_tasks_payload, dict) else [])
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        ]

        pattern_candidates: List[Dict[str, Any]] = []
        for category in categories_below:
            cat = str(category).strip()
            if not cat:
                continue
            pattern_candidates.append(
                {
                    "pattern_id": f"low_category:{cat}",
                    "pattern_type": "low_category",
                    "category": cat,
                    "trigger": "quality_gate_below_threshold",
                    "recommended_actions": next_titles[:3],
                }
            )
        for category in persistent_low:
            cat = str(category).strip()
            if not cat:
                continue
            pattern_candidates.append(
                {
                    "pattern_id": f"persistent_low:{cat}",
                    "pattern_type": "persistent_low",
                    "category": cat,
                    "trigger": "trend_persistent_low",
                    "recommended_actions": next_titles[:3],
                }
            )
        for category in stagnant:
            cat = str(category).strip()
            if not cat:
                continue
            pattern_candidates.append(
                {
                    "pattern_id": f"stagnant:{cat}",
                    "pattern_type": "stagnant_category",
                    "category": cat,
                    "trigger": "trend_stagnation",
                    "recommended_actions": next_titles[:3],
                }
            )
        if bool(loop_state.get("repeated_issue_limit_hit")):
            pattern_candidates.append(
                {
                    "pattern_id": "loop_guard:repeated_issue",
                    "pattern_type": "loop_guard",
                    "category": "",
                    "trigger": "repeated_issue_limit_hit",
                    "recommended_actions": next_titles[:3],
                }
            )
        if bool(loop_state.get("score_stagnation_detected")):
            pattern_candidates.append(
                {
                    "pattern_id": "loop_guard:score_stagnation",
                    "pattern_type": "loop_guard",
                    "category": "",
                    "trigger": "score_stagnation_detected",
                    "recommended_actions": next_titles[:3],
                }
            )
        if bool(loop_state.get("quality_regression_detected")):
            pattern_candidates.append(
                {
                    "pattern_id": "loop_guard:quality_regression",
                    "pattern_type": "loop_guard",
                    "category": "",
                    "trigger": "quality_regression_detected",
                    "recommended_actions": next_titles[:3],
                }
            )

        for candidate in pattern_candidates:
            pattern_id = str(candidate.get("pattern_id", "")).strip()
            if not pattern_id:
                continue
            current = merged.get(
                pattern_id,
                {
                    "pattern_id": pattern_id,
                    "pattern_type": str(candidate.get("pattern_type", "")).strip(),
                    "category": str(candidate.get("category", "")).strip(),
                    "trigger": str(candidate.get("trigger", "")).strip(),
                    "count": 0,
                    "first_seen_at": generated_at,
                    "last_seen_at": generated_at,
                    "recommended_actions": [],
                },
            )
            current["count"] = int(current.get("count", 0) or 0) + 1
            current["last_seen_at"] = generated_at
            current["recommended_actions"] = list(candidate.get("recommended_actions", []) or [])
            merged[pattern_id] = current

        ordered_items = sorted(
            merged.values(),
            key=lambda item: (-int(item.get("count", 0) or 0), str(item.get("pattern_id", ""))),
        )
        failure_patterns_path.write_text(
            json.dumps({"generated_at": generated_at, "items": ordered_items[:100]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_conventions_artifact(
        self,
        *,
        repository_path: Path,
        conventions_path: Path,
        job: JobRecord,
        generated_at: str,
    ) -> None:
        """Write convention snapshot from repo structure and manifests."""

        rules: List[Dict[str, Any]] = []
        detected_stack: List[str] = []

        def add_rule(rule_id: str, convention_type: str, rule: str, evidence_paths: List[str], confidence: float) -> None:
            rules.append(
                {
                    "id": rule_id,
                    "type": convention_type,
                    "rule": rule,
                    "evidence_paths": evidence_paths,
                    "confidence": confidence,
                }
            )

        def add_stack(tag: str) -> None:
            normalized = str(tag or "").strip().lower()
            if normalized and normalized not in detected_stack:
                detected_stack.append(normalized)

        package_json = self.read_json_file(repository_path / "package.json")
        package_deps = self.package_dependency_map(package_json)
        pyproject_text = self.read_text_file(repository_path / "pyproject.toml")
        requirements_text = self.read_text_file(repository_path / "requirements.txt")

        if (repository_path / "tests").exists():
            add_rule("conv_tests_dir", "filesystem", "Tests live under tests/", ["tests"], 0.74)
        if (repository_path / "tests" / "e2e").exists():
            add_rule("conv_tests_e2e_dir", "testing", "End-to-end tests live under tests/e2e/", ["tests/e2e"], 0.8)
        if (repository_path / "src").exists():
            add_rule("conv_src_dir", "filesystem", "Primary source files live under src/", ["src"], 0.72)
        if (repository_path / "app").exists():
            add_rule("conv_app_dir", "filesystem", "Primary application code lives under app/", ["app"], 0.72)
        if (repository_path / "app" / "components").exists():
            add_rule("conv_app_components", "ui_structure", "Reusable UI components live under app/components/", ["app/components"], 0.82)
        if (repository_path / "components").exists():
            add_rule("conv_components_dir", "ui_structure", "Reusable UI components live under components/", ["components"], 0.8)
        if (repository_path / "src" / "components").exists():
            add_rule("conv_src_components_dir", "ui_structure", "Reusable UI components live under src/components/", ["src/components"], 0.82)
        if (repository_path / "package.json").exists():
            add_rule("conv_node_runtime", "runtime", "Node package manifest is package.json", ["package.json"], 0.9)
            add_stack("node")
        if (repository_path / "pyproject.toml").exists():
            add_rule("conv_pyproject", "runtime", "Python project metadata is pyproject.toml", ["pyproject.toml"], 0.9)
            add_stack("python")
        elif (repository_path / "requirements.txt").exists():
            add_rule("conv_requirements", "runtime", "Python dependencies are managed with requirements.txt", ["requirements.txt"], 0.86)
            add_stack("python")
        if (repository_path / "README.md").exists():
            add_rule("conv_readme", "documentation", "Repository keeps top-level README.md", ["README.md"], 0.66)

        if package_deps:
            if "next" in package_deps:
                add_stack("nextjs")
                add_rule("conv_nextjs", "framework", "Frontend framework is Next.js", ["package.json"], 0.92)
            if "react" in package_deps:
                add_stack("react")
                add_rule("conv_react", "framework", "UI layer is based on React", ["package.json"], 0.9)
            if "react-native" in package_deps:
                add_stack("react-native")
                add_rule("conv_react_native", "framework", "App layer is based on React Native", ["package.json"], 0.92)
            if "tailwindcss" in package_deps:
                add_stack("tailwindcss")
                add_rule("conv_tailwindcss", "styling", "Styling uses Tailwind CSS utilities", ["package.json"], 0.9)
            if "framer-motion" in package_deps:
                add_stack("framer-motion")
                add_rule("conv_framer_motion", "animation", "Motion/animation uses framer-motion", ["package.json"], 0.88)
            if "lucide-react" in package_deps:
                add_stack("lucide-react")
                add_rule("conv_lucide_react", "icons", "Icons use lucide-react", ["package.json"], 0.88)
            if "@playwright/test" in package_deps or "playwright" in package_deps:
                add_stack("playwright")
                add_rule("conv_playwright", "testing", "Browser/E2E tests use Playwright", ["package.json"], 0.9)
            if "vitest" in package_deps:
                add_stack("vitest")
                add_rule("conv_vitest", "testing", "Unit/integration tests use Vitest", ["package.json"], 0.88)
            if "jest" in package_deps:
                add_stack("jest")
                add_rule("conv_jest", "testing", "Unit/integration tests use Jest", ["package.json"], 0.88)
            if "typescript" in package_deps or (repository_path / "tsconfig.json").exists():
                add_stack("typescript")
                add_rule("conv_typescript", "language", "Source is authored in TypeScript", ["package.json", "tsconfig.json"], 0.86)

        py_lower = pyproject_text.lower()
        req_lower = requirements_text.lower()
        if "fastapi" in py_lower or "fastapi" in req_lower:
            add_stack("fastapi")
            add_rule(
                "conv_fastapi",
                "framework",
                "Backend/API layer uses FastAPI",
                ["pyproject.toml" if pyproject_text else "requirements.txt"],
                0.9,
            )
        if "pytest" in py_lower or "pytest" in req_lower:
            add_stack("pytest")
            add_rule(
                "conv_pytest",
                "testing",
                "Python tests use pytest",
                ["pyproject.toml" if pyproject_text else "requirements.txt"],
                0.88,
            )

        if (repository_path / "app" / "layout.tsx").exists() or (repository_path / "app" / "page.tsx").exists():
            add_rule("conv_next_app_router", "routing", "Next.js app router uses app/ directory entrypoints", ["app/layout.tsx", "app/page.tsx"], 0.84)
        if (repository_path / "pages").exists():
            add_rule("conv_pages_router", "routing", "Page routes live under pages/", ["pages"], 0.78)

        component_extensions = self.detect_component_extension_preference(repository_path)
        if component_extensions["tsx"] > 0 and component_extensions["tsx"] >= component_extensions["jsx"]:
            add_rule(
                "conv_component_tsx",
                "language",
                "Component implementations prefer .tsx files",
                component_extensions["evidence_paths"][:3],
                0.76,
            )
        elif component_extensions["jsx"] > 0:
            add_rule(
                "conv_component_jsx",
                "language",
                "Component implementations prefer .jsx files",
                component_extensions["evidence_paths"][:3],
                0.72,
            )

        test_convention = self.detect_test_file_conventions(repository_path)
        if test_convention["python"] > 0:
            add_rule(
                "conv_pytest_file_pattern",
                "testing",
                "Python tests follow test_*.py naming under tests/",
                test_convention["python_paths"][:3],
                0.78,
            )
        if test_convention["js"] > 0:
            add_rule(
                "conv_js_test_pattern",
                "testing",
                "Frontend tests use *.test.* or *.spec.* naming",
                test_convention["js_paths"][:3],
                0.76,
            )

        payload = {
            "generated_at": generated_at,
            "job_id": job.job_id,
            "app_code": job.app_code,
            "repository": self.job_execution_repository(job),
            "detected_stack": sorted(detected_stack),
            "rules": sorted(rules, key=lambda item: str(item.get("id", ""))),
        }
        conventions_path.parent.mkdir(parents=True, exist_ok=True)
        conventions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def package_dependency_map(package_json: Dict[str, Any]) -> Dict[str, str]:
        """Return merged dependency map from package.json payload."""

        merged: Dict[str, str] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            payload = package_json.get(section, {}) if isinstance(package_json, dict) else {}
            if not isinstance(payload, dict):
                continue
            for key, value in payload.items():
                merged[str(key)] = str(value)
        return merged

    @staticmethod
    def detect_component_extension_preference(repository_path: Path) -> Dict[str, Any]:
        """Detect preferred component file extension under conventional component dirs."""

        candidate_dirs = [
            repository_path / "app" / "components",
            repository_path / "src" / "components",
            repository_path / "components",
        ]
        counts = {"tsx": 0, "jsx": 0, "evidence_paths": []}
        for candidate_dir in candidate_dirs:
            if not candidate_dir.exists():
                continue
            for pattern, key in (("*.tsx", "tsx"), ("*.jsx", "jsx")):
                for path in sorted(candidate_dir.rglob(pattern))[:10]:
                    counts[key] += 1
                    if len(counts["evidence_paths"]) < 6:
                        counts["evidence_paths"].append(str(path.relative_to(repository_path)))
        return counts

    @staticmethod
    def detect_test_file_conventions(repository_path: Path) -> Dict[str, Any]:
        """Detect conventional Python/JS test file naming patterns."""

        python_paths = (
            [str(path.relative_to(repository_path)) for path in sorted((repository_path / "tests").rglob("test_*.py"))[:6]]
            if (repository_path / "tests").exists()
            else []
        )
        js_patterns = ["*.test.ts", "*.test.tsx", "*.test.js", "*.test.jsx", "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.spec.jsx"]
        js_paths: List[str] = []
        for base_dir in [repository_path / "tests", repository_path / "src", repository_path / "app", repository_path]:
            if not base_dir.exists():
                continue
            for pattern in js_patterns:
                for path in sorted(base_dir.rglob(pattern)):
                    relative = str(path.relative_to(repository_path))
                    if relative not in js_paths:
                        js_paths.append(relative)
                    if len(js_paths) >= 6:
                        break
                if len(js_paths) >= 6:
                    break
            if len(js_paths) >= 6:
                break
        return {
            "python": len(python_paths),
            "python_paths": python_paths,
            "js": len(js_paths),
            "js_paths": js_paths,
        }
