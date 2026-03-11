"""Optional LangGraph shadow trace for the planner refinement loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.metadata
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from app.models import utc_now_iso


class PlannerShadowState(TypedDict):
    """Minimal graph state for planner shadow replay."""

    rounds: List[Dict[str, Any]]
    index: int
    trace: List[Dict[str, Any]]


@dataclass(frozen=True)
class LangGraphPlannerShadowResult:
    """Serializable shadow trace payload."""

    enabled: bool
    available: bool
    status: str
    detail: str
    framework: str
    framework_version: str
    generated_at: str
    planning_mode: str
    max_rounds: int
    round_count: int
    final_passed: bool
    contract_preserved: bool
    plan_contract: Dict[str, Any]
    trace: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_disabled_planner_shadow_payload(*, detail: str = "disabled") -> Dict[str, Any]:
    """Return inert payload when planner shadow is off."""

    return LangGraphPlannerShadowResult(
        enabled=False,
        available=False,
        status="disabled",
        detail=detail,
        framework="langgraph",
        framework_version="",
        generated_at=utc_now_iso(),
        planning_mode="",
        max_rounds=0,
        round_count=0,
        final_passed=False,
        contract_preserved=False,
        plan_contract={},
        trace=[],
    ).to_dict()


class LangGraphPlannerShadowRunner:
    """Replay planner rounds through a tiny LangGraph for traceability only."""

    def run(
        self,
        *,
        rounds: List[Dict[str, Any]],
        max_rounds: int,
        planning_mode: str,
        plan_path: Path,
        plan_quality_path: Path,
    ) -> Dict[str, Any]:
        """Run the optional shadow graph and return trace payload."""

        try:
            from langgraph.graph import END, START, StateGraph
        except Exception as error:  # noqa: BLE001
            return LangGraphPlannerShadowResult(
                enabled=True,
                available=False,
                status="unavailable",
                detail=f"langgraph_import_failed: {error}",
                framework="langgraph",
                framework_version="",
                generated_at=utc_now_iso(),
                planning_mode=str(planning_mode or "").strip(),
                max_rounds=max(1, int(max_rounds or 1)),
                round_count=len(rounds),
                final_passed=bool(self._final_quality(rounds).get("passed")),
                contract_preserved=plan_path.exists() and plan_quality_path.exists(),
                plan_contract=self._plan_contract(plan_path=plan_path, plan_quality_path=plan_quality_path),
                trace=[],
            ).to_dict()

        normalized_rounds = [item for item in rounds if isinstance(item, dict)]
        if not normalized_rounds:
            return LangGraphPlannerShadowResult(
                enabled=True,
                available=True,
                status="no_rounds",
                detail="planner_rounds_missing",
                framework="langgraph",
                framework_version=self._langgraph_version(),
                generated_at=utc_now_iso(),
                planning_mode=str(planning_mode or "").strip(),
                max_rounds=max(1, int(max_rounds or 1)),
                round_count=0,
                final_passed=False,
                contract_preserved=plan_path.exists() and plan_quality_path.exists(),
                plan_contract=self._plan_contract(plan_path=plan_path, plan_quality_path=plan_quality_path),
                trace=[],
            ).to_dict()

        graph = StateGraph(PlannerShadowState)
        graph.add_node("draft_plan", self._draft_plan)
        graph.add_node("evaluate_plan", self._evaluate_plan)
        graph.add_node("optional_tool_request", self._optional_tool_request)
        graph.add_node("refine_plan", self._refine_plan)
        graph.add_edge(START, "draft_plan")
        graph.add_edge("draft_plan", "evaluate_plan")
        graph.add_edge("evaluate_plan", "optional_tool_request")
        graph.add_conditional_edges(
            "optional_tool_request",
            self._route_after_optional_tool,
            {
                "refine_plan": "refine_plan",
                "end": END,
            },
        )
        graph.add_edge("refine_plan", "draft_plan")

        try:
            app = graph.compile()
            final_state = app.invoke(
                {
                    "rounds": normalized_rounds,
                    "index": 0,
                    "trace": [],
                }
            )
            trace = final_state.get("trace", []) if isinstance(final_state, dict) else []
            status = "completed"
            detail = "shadow_trace_ok"
        except Exception as error:  # noqa: BLE001
            trace = []
            status = "failed"
            detail = f"shadow_graph_failed: {error}"

        return LangGraphPlannerShadowResult(
            enabled=True,
            available=True,
            status=status,
            detail=detail,
            framework="langgraph",
            framework_version=self._langgraph_version(),
            generated_at=utc_now_iso(),
            planning_mode=str(planning_mode or "").strip(),
            max_rounds=max(1, int(max_rounds or 1)),
            round_count=len(normalized_rounds),
            final_passed=bool(self._final_quality(normalized_rounds).get("passed")),
            contract_preserved=plan_path.exists() and plan_quality_path.exists(),
            plan_contract=self._plan_contract(plan_path=plan_path, plan_quality_path=plan_quality_path),
            trace=trace,
        ).to_dict()

    @staticmethod
    def _langgraph_version() -> str:
        try:
            return importlib.metadata.version("langgraph")
        except importlib.metadata.PackageNotFoundError:
            return ""

    @staticmethod
    def _round_payload(state: PlannerShadowState) -> Dict[str, Any]:
        rounds = state.get("rounds", [])
        index = max(0, min(int(state.get("index", 0) or 0), max(len(rounds) - 1, 0)))
        return rounds[index] if rounds else {}

    @classmethod
    def _draft_plan(cls, state: PlannerShadowState) -> Dict[str, Any]:
        current = cls._round_payload(state)
        return {
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "draft_plan",
                    "round": int(current.get("round", 0) or 0),
                    "mode": str(current.get("mode", "")).strip(),
                }
            ]
        }

    @classmethod
    def _evaluate_plan(cls, state: PlannerShadowState) -> Dict[str, Any]:
        current = cls._round_payload(state)
        quality = current.get("quality", {}) if isinstance(current.get("quality"), dict) else {}
        return {
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "evaluate_plan",
                    "round": int(current.get("round", 0) or 0),
                    "passed": bool(quality.get("passed")),
                    "score": int(quality.get("score", 0) or 0),
                    "missing_sections": list(quality.get("missing_sections", []) or []),
                }
            ]
        }

    @classmethod
    def _optional_tool_request(cls, state: PlannerShadowState) -> Dict[str, Any]:
        current = cls._round_payload(state)
        tool_requests = int(current.get("tool_requests", 0) or 0)
        return {
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "optional_tool_request",
                    "round": int(current.get("round", 0) or 0),
                    "tool_requests": tool_requests,
                    "used": tool_requests > 0,
                }
            ]
        }

    @classmethod
    def _refine_plan(cls, state: PlannerShadowState) -> Dict[str, Any]:
        rounds = state.get("rounds", [])
        current = cls._round_payload(state)
        current_index = int(state.get("index", 0) or 0)
        next_index = min(current_index + 1, max(len(rounds) - 1, 0))
        next_round = rounds[next_index] if rounds else {}
        return {
            "index": next_index,
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "refine_plan",
                    "from_round": int(current.get("round", 0) or 0),
                    "to_round": int(next_round.get("round", 0) or 0),
                    "reason": "quality_not_passed",
                }
            ],
        }

    @classmethod
    def _route_after_optional_tool(cls, state: PlannerShadowState) -> str:
        rounds = state.get("rounds", [])
        current = cls._round_payload(state)
        quality = current.get("quality", {}) if isinstance(current.get("quality"), dict) else {}
        passed = bool(quality.get("passed"))
        index = int(state.get("index", 0) or 0)
        has_next_round = index + 1 < len(rounds)
        if passed or not has_next_round:
            return "end"
        return "refine_plan"

    @classmethod
    def _final_quality(cls, rounds: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rounds:
            return {}
        quality = rounds[-1].get("quality", {})
        return quality if isinstance(quality, dict) else {}

    @staticmethod
    def _plan_contract(*, plan_path: Path, plan_quality_path: Path) -> Dict[str, Any]:
        return {
            "plan_path": str(plan_path),
            "plan_exists": plan_path.exists(),
            "plan_quality_path": str(plan_quality_path),
            "plan_quality_exists": plan_quality_path.exists(),
        }
