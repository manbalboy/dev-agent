"""Planner graph MVP utilities.

This module provides deterministic quality checks for PLAN.md so orchestration
can run iterative planner refinement without hard-depending on one-shot output.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


def evaluate_plan_markdown(plan_text: str) -> Dict[str, Any]:
    """Evaluate PLAN.md quality with lightweight deterministic checks."""

    text = (plan_text or "").strip()
    findings: List[Dict[str, str]] = []
    score = 100

    if not text:
        return {
            "passed": False,
            "score": 0,
            "missing_sections": ["all"],
            "findings": [{"code": "EMPTY_PLAN", "detail": "PLAN.md is empty"}],
        }

    required = [
        ("task_breakdown", [r"task breakdown", r"작업 분해", r"우선순위"]),
        ("mvp_scope", [r"mvp", r"in[- ]scope", r"out[- ]of[- ]scope", r"비범위"]),
        ("completion_criteria", [r"completion criteria", r"완료 조건"]),
        ("risk_test", [r"risk", r"테스트", r"test strategy"]),
        ("design_intent", [r"design intent", r"디자인", r"스타일"]),
        ("extensible_architecture", [r"extensible architecture", r"확장", r"모듈 경계", r"인터페이스", r"확장 포인트"]),
        ("mvp_phases", [r"phase", r"단계", r"m1", r"m2", r"deliver"]),
    ]

    missing_sections: List[str] = []
    lowered = text.lower()
    for section_key, patterns in required:
        matched = any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)
        if not matched:
            missing_sections.append(section_key)
            findings.append({"code": "MISSING_SECTION", "detail": section_key})
            score -= 14

    if len(text) < 600:
        findings.append({"code": "TOO_SHORT", "detail": f"len={len(text)}"})
        score -= 10
    if "```" in text:
        findings.append({"code": "HAS_CODE_BLOCK", "detail": "planner output should be plain markdown body"})
        score -= 5
    if "포트" in text and not re.search(r"3\d{3}", text):
        findings.append({"code": "PORT_RULE_MISSING", "detail": "mentions port but not 3000-range"})
        score -= 6

    score = max(0, score)
    passed = score >= 80 and not missing_sections
    return {
        "passed": passed,
        "score": score,
        "missing_sections": missing_sections,
        "findings": findings,
    }


def build_refinement_instruction(
    *,
    round_index: int,
    quality: Dict[str, Any],
) -> str:
    """Create concise refinement instruction for the next planner round."""

    missing = quality.get("missing_sections", []) or []
    findings = quality.get("findings", []) or []
    missing_text = ", ".join(str(item) for item in missing) if missing else "-"
    finding_text = "; ".join(
        f"{item.get('code')}({item.get('detail')})"
        for item in findings[:5]
        if isinstance(item, dict)
    ) or "-"
    return (
        f"\n\n[PlannerGraph MVP - round {round_index} refinement]\n"
        "아래 PLAN 품질 피드백을 반영해 PLAN.md를 전체 재작성하세요.\n"
        f"- missing_sections: {missing_text}\n"
        f"- findings: {finding_text}\n"
        "- 기존 문서의 의도를 유지하되 누락 섹션과 검증 가능성을 보강하세요.\n"
        "- 출력은 PLAN.md markdown 본문만 허용합니다.\n"
    )
