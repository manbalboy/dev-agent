"""Optional LangGraph shadow trace for recovery flow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.metadata
from typing import Any, Dict, List, TypedDict

from app.models import utc_now_iso


class RecoveryShadowState(TypedDict):
    """Minimal state for one recovery shadow replay."""

    session: Dict[str, Any]
    trace: List[Dict[str, Any]]


@dataclass(frozen=True)
class LangGraphRecoveryShadowSession:
    """Serializable session payload for one recovery attempt."""

    generated_at: str
    stage: str
    gate_label: str
    reason: str
    analysis_written: bool
    recoverable: bool
    recovery_attempted: bool
    recovery_succeeded: bool
    framework: str
    framework_version: str
    status: str
    detail: str
    trace: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_disabled_recovery_shadow_payload(*, detail: str = "disabled") -> Dict[str, Any]:
    """Return inert payload when recovery shadow is off."""

    return {
        "enabled": False,
        "available": False,
        "status": "disabled",
        "detail": detail,
        "framework": "langgraph",
        "framework_version": "",
        "generated_at": utc_now_iso(),
        "session_count": 0,
        "sessions": [],
    }


class LangGraphRecoveryShadowRunner:
    """Replay observed recovery flow decisions through a tiny LangGraph."""

    def run(
        self,
        *,
        stage: str,
        gate_label: str,
        reason: str,
        analysis_written: bool,
        recoverable: bool,
        recovery_attempted: bool,
        recovery_succeeded: bool,
    ) -> Dict[str, Any]:
        """Build one recovery shadow session."""

        try:
            from langgraph.graph import END, START, StateGraph
        except Exception as error:  # noqa: BLE001
            return LangGraphRecoveryShadowSession(
                generated_at=utc_now_iso(),
                stage=str(stage or "").strip(),
                gate_label=str(gate_label or "").strip(),
                reason=str(reason or "").strip(),
                analysis_written=bool(analysis_written),
                recoverable=bool(recoverable),
                recovery_attempted=bool(recovery_attempted),
                recovery_succeeded=bool(recovery_succeeded),
                framework="langgraph",
                framework_version="",
                status="unavailable",
                detail=f"langgraph_import_failed: {error}",
                trace=[],
            ).to_dict()

        graph = StateGraph(RecoveryShadowState)
        graph.add_node("analyze_failure", self._analyze_failure)
        graph.add_node("decide_recoverable", self._decide_recoverable)
        graph.add_node("fix_once", self._fix_once)
        graph.add_node("retest", self._retest)
        graph.add_edge(START, "analyze_failure")
        graph.add_edge("analyze_failure", "decide_recoverable")
        graph.add_conditional_edges(
            "decide_recoverable",
            self._route_after_decision,
            {
                "fix_once": "fix_once",
                "end": END,
            },
        )
        graph.add_edge("fix_once", "retest")
        graph.add_edge("retest", END)

        try:
            app = graph.compile()
            final_state = app.invoke(
                {
                    "session": {
                        "stage": str(stage or "").strip(),
                        "gate_label": str(gate_label or "").strip(),
                        "reason": str(reason or "").strip(),
                        "analysis_written": bool(analysis_written),
                        "recoverable": bool(recoverable),
                        "recovery_attempted": bool(recovery_attempted),
                        "recovery_succeeded": bool(recovery_succeeded),
                    },
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

        return LangGraphRecoveryShadowSession(
            generated_at=utc_now_iso(),
            stage=str(stage or "").strip(),
            gate_label=str(gate_label or "").strip(),
            reason=str(reason or "").strip(),
            analysis_written=bool(analysis_written),
            recoverable=bool(recoverable),
            recovery_attempted=bool(recovery_attempted),
            recovery_succeeded=bool(recovery_succeeded),
            framework="langgraph",
            framework_version=self._langgraph_version(),
            status=status,
            detail=detail,
            trace=trace,
        ).to_dict()

    @staticmethod
    def _langgraph_version() -> str:
        try:
            return importlib.metadata.version("langgraph")
        except importlib.metadata.PackageNotFoundError:
            return ""

    @staticmethod
    def _analyze_failure(state: RecoveryShadowState) -> Dict[str, Any]:
        session = state.get("session", {})
        return {
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "analyze_failure",
                    "analysis_written": bool(session.get("analysis_written")),
                    "gate_label": str(session.get("gate_label", "")).strip(),
                }
            ]
        }

    @staticmethod
    def _decide_recoverable(state: RecoveryShadowState) -> Dict[str, Any]:
        session = state.get("session", {})
        return {
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "decide_recoverable",
                    "recoverable": bool(session.get("recoverable")),
                    "reason": str(session.get("reason", "")).strip()[:300],
                }
            ]
        }

    @staticmethod
    def _fix_once(state: RecoveryShadowState) -> Dict[str, Any]:
        session = state.get("session", {})
        return {
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "fix_once",
                    "attempted": bool(session.get("recovery_attempted")),
                }
            ]
        }

    @staticmethod
    def _retest(state: RecoveryShadowState) -> Dict[str, Any]:
        session = state.get("session", {})
        return {
            "trace": list(state.get("trace", []))
            + [
                {
                    "node": "retest",
                    "passed": bool(session.get("recovery_succeeded")),
                }
            ]
        }

    @staticmethod
    def _route_after_decision(state: RecoveryShadowState) -> str:
        session = state.get("session", {})
        if bool(session.get("recoverable")) and bool(session.get("recovery_attempted")):
            return "fix_once"
        return "end"
