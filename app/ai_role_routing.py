"""AI role routing helpers.

Logical pipeline roles should stay stable even when operators swap which AI CLI
backs each responsibility. This module keeps that mapping in configuration so
the orchestrator can resolve one route to a compatible command template family.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _normalize_identifier(value: str, max_length: int = 64) -> str:
    """Normalize one route/role identifier into a safe lowercase token."""

    lowered = (value or "").strip().lower()
    filtered = "".join(ch for ch in lowered if ch.isalnum() or ch in {"-", "_"})
    return filtered[:max_length]


def default_ai_role_routing_payload() -> Dict[str, Any]:
    """Return the default AI routing policy.

    Current policy:
    - Gemini: planning/review
    - Codex: coding/expert work plus auxiliary helper tasks
    """

    return {
        "version": 1,
        "strategy": {
            "primary_planning_provider": "gemini",
            "primary_review_provider": "gemini",
            "primary_coding_provider": "codex",
            "auxiliary_providers": ["codex"],
        },
        "routes": {
            "planner": {
                "role_code": "architect",
                "template_keys": ["planner"],
                "description": "제품 정의, 설계, 구현 계획",
            },
            "reviewer": {
                "role_code": "reviewer",
                "template_keys": ["reviewer"],
                "description": "품질 리뷰와 개선 우선순위 판단",
            },
            "coder": {
                "role_code": "coder",
                "template_keys": ["coder"],
                "description": "기능 구현과 수정",
            },
            "designer": {
                "role_code": "designer",
                "template_keys": ["coder"],
                "description": "디자인 시스템/UX 산출물 구현",
            },
            "publisher": {
                "role_code": "publisher",
                "template_keys": ["coder"],
                "description": "퍼블리싱과 handoff 반영",
            },
            "copywriter": {
                "role_code": "copywriter",
                "template_keys": ["coder"],
                "description": "고객-facing 카피 산출물 작성",
            },
            "documentation": {
                "role_code": "tech-writer",
                "template_keys": [
                    "documentation_writer",
                    "pr_summary",
                    "commit_summary",
                    "escalation",
                ],
                "fallback_route": "coder",
                "description": "기술 문서 번들 작성",
            },
            "commit_summary": {
                "role_code": "tech-writer",
                "template_keys": ["commit_summary", "pr_summary", "escalation"],
                "fallback_route": "copilot_helper",
                "description": "커밋 제목/요약 생성",
            },
            "pr_summary": {
                "role_code": "tech-writer",
                "template_keys": ["pr_summary", "escalation"],
                "fallback_route": "copilot_helper",
                "description": "PR 본문 요약 생성",
            },
            "escalation": {
                "role_code": "escalation-helper",
                "template_keys": ["escalation", "copilot"],
                "fallback_route": "copilot_helper",
                "description": "실패 분석과 보조 전략",
            },
            "copilot_helper": {
                "role_code": "orchestration-helper",
                "template_keys": ["copilot"],
                "description": "보조 오케스트레이션 분석",
            },
            "research_search": {
                "role_code": "research-agent",
                "template_keys": ["research_search"],
                "description": "검색 기반 리서치 컨텍스트 생성",
            },
        },
    }


def read_ai_role_routing_payload(path: Path) -> Dict[str, Any]:
    """Load routing policy JSON with safe defaults and route merging."""

    defaults = default_ai_role_routing_payload()
    if not path.exists():
        return defaults

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaults

    if not isinstance(payload, dict):
        return defaults

    strategy = defaults["strategy"].copy()
    raw_strategy = payload.get("strategy")
    if isinstance(raw_strategy, dict):
        for key, value in raw_strategy.items():
            if key == "auxiliary_providers" and isinstance(value, list):
                cleaned = [
                    _normalize_identifier(str(item), max_length=32)
                    for item in value
                    if _normalize_identifier(str(item), max_length=32)
                ]
                if cleaned:
                    strategy[key] = cleaned
                continue
            if isinstance(value, str) and value.strip():
                strategy[key] = value.strip().lower()

    routes = {
        name: dict(config)
        for name, config in defaults["routes"].items()
    }
    raw_routes = payload.get("routes")
    if isinstance(raw_routes, dict):
        for raw_name, raw_config in raw_routes.items():
            route_name = _normalize_identifier(str(raw_name))
            if not route_name:
                continue
            normalized = _normalize_route_config(raw_config)
            if normalized is None:
                continue
            merged = dict(routes.get(route_name, {}))
            merged.update(normalized)
            routes[route_name] = merged

    return {
        "version": int(payload.get("version", defaults["version"])),
        "strategy": strategy,
        "routes": routes,
    }


def write_ai_role_routing_payload(path: Path, payload: Dict[str, Any]) -> None:
    """Persist routing policy after applying the same normalization rules."""

    normalized = read_ai_role_routing_payload_from_object(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_ai_role_routing_payload_from_object(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one in-memory payload using the same merge behavior as file load."""

    defaults = default_ai_role_routing_payload()
    if not isinstance(payload, dict):
        return defaults

    strategy = defaults["strategy"].copy()
    raw_strategy = payload.get("strategy")
    if isinstance(raw_strategy, dict):
        for key, value in raw_strategy.items():
            if key == "auxiliary_providers" and isinstance(value, list):
                cleaned = [
                    _normalize_identifier(str(item), max_length=32)
                    for item in value
                    if _normalize_identifier(str(item), max_length=32)
                ]
                if cleaned:
                    strategy[key] = cleaned
                continue
            if isinstance(value, str) and value.strip():
                strategy[key] = value.strip().lower()

    routes = {
        name: dict(config)
        for name, config in defaults["routes"].items()
    }
    raw_routes = payload.get("routes")
    if isinstance(raw_routes, dict):
        for raw_name, raw_config in raw_routes.items():
            route_name = _normalize_identifier(str(raw_name))
            if not route_name:
                continue
            normalized = _normalize_route_config(raw_config)
            if normalized is None:
                continue
            merged = dict(routes.get(route_name, {}))
            merged.update(normalized)
            routes[route_name] = merged

    return {
        "version": int(payload.get("version", defaults["version"])),
        "strategy": strategy,
        "routes": routes,
    }


def _normalize_route_config(raw_config: Any) -> Dict[str, Any] | None:
    """Normalize one route config entry."""

    if isinstance(raw_config, str):
        role_code = _normalize_identifier(raw_config)
        if not role_code:
            return None
        return {"role_code": role_code}

    if not isinstance(raw_config, dict):
        return None

    normalized: Dict[str, Any] = {}
    role_code = _normalize_identifier(str(raw_config.get("role_code", "")))
    if role_code:
        normalized["role_code"] = role_code

    template_keys = _normalize_template_keys(raw_config.get("template_keys"))
    if template_keys:
        normalized["template_keys"] = template_keys

    fallback_route = _normalize_identifier(str(raw_config.get("fallback_route", "")))
    if fallback_route:
        normalized["fallback_route"] = fallback_route

    description = str(raw_config.get("description", "")).strip()
    if description:
        normalized["description"] = description

    return normalized or None


def _normalize_template_keys(raw_value: Any) -> List[str]:
    """Normalize route template keys into a compact list."""

    if not isinstance(raw_value, list):
        return []
    result: List[str] = []
    for item in raw_value:
        key = _normalize_identifier(str(item), max_length=80)
        if key and key not in result:
            result.append(key)
    return result


def _normalize_string_list(raw_value: Any, *, max_length: int = 80) -> List[str]:
    """Normalize optional list metadata into stable identifiers."""

    if isinstance(raw_value, str):
        values = [part.strip() for part in raw_value.replace("\n", ",").split(",")]
    elif isinstance(raw_value, list):
        values = [str(item).strip() for item in raw_value]
    else:
        return []

    result: List[str] = []
    for item in values:
        key = _normalize_identifier(item, max_length=max_length)
        if key and key not in result:
            result.append(key)
    return result


def _read_roles_index(path: Path) -> Dict[str, Dict[str, Any]]:
    """Read enabled role rows into an index keyed by role code."""

    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    roles: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("roles", []):
        if not isinstance(item, dict):
            continue
        code = _normalize_identifier(str(item.get("code", "")))
        if not code:
            continue
        roles[code] = {
            "code": code,
            "name": str(item.get("name", "")).strip(),
            "cli": _normalize_identifier(str(item.get("cli", "")), max_length=32),
            "template_key": _normalize_identifier(str(item.get("template_key", "")), max_length=80),
            "objective": str(item.get("objective", "")).strip(),
            "inputs": str(item.get("inputs", "")).strip(),
            "outputs": str(item.get("outputs", "")).strip(),
            "checklist": str(item.get("checklist", "")).strip(),
            "skills": _normalize_string_list(item.get("skills")),
            "allowed_tools": _normalize_string_list(item.get("allowed_tools")),
            "enabled": bool(item.get("enabled", True)),
        }
    return roles


def _read_presets_index(path: Path, known_roles: Dict[str, Dict[str, Any]]) -> Dict[str, Tuple[str, ...]]:
    """Read role preset rows into an index keyed by preset id."""

    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    presets: Dict[str, Tuple[str, ...]] = {}
    for item in payload.get("presets", []):
        if not isinstance(item, dict):
            continue
        preset_id = _normalize_identifier(str(item.get("preset_id", "")))
        if not preset_id:
            continue
        role_codes: List[str] = []
        for raw_code in item.get("role_codes", []):
            code = _normalize_identifier(str(raw_code))
            if code and code in known_roles and code not in role_codes:
                role_codes.append(code)
        presets[preset_id] = tuple(role_codes)
    return presets


def _preferred_cli_for_route(route_name: str) -> str:
    """Return the default provider bias for one logical route."""

    if route_name in {"planner", "reviewer"}:
        return "gemini"
    if route_name == "research_search":
        return "python3"
    return "codex"


def _select_role_override_from_preset(
    *,
    route_name: str,
    preset_id: str,
    roles: Dict[str, Dict[str, Any]],
    presets: Dict[str, Tuple[str, ...]],
    route_config: Dict[str, Any],
    default_config: Dict[str, Any],
) -> str:
    """Pick the most compatible role from one preset for a logical route."""

    preset_roles = presets.get(preset_id, ())
    if not preset_roles:
        return ""

    template_keys = tuple(
        _normalize_template_keys(route_config.get("template_keys"))
        or _normalize_template_keys(default_config.get("template_keys"))
    )
    default_role_code = _normalize_identifier(str(route_config.get("role_code", ""))) or _normalize_identifier(
        str(default_config.get("role_code", ""))
    )
    preferred_cli = _preferred_cli_for_route(route_name)

    ranked: List[Tuple[int, int, str]] = []
    for index, role_code in enumerate(preset_roles):
        role = roles.get(role_code)
        if role is None or not role.get("enabled", True):
            continue
        score = 0
        if role_code == default_role_code:
            score += 100
        role_template = _normalize_identifier(str(role.get("template_key", "")), max_length=80)
        if role_template and role_template in template_keys:
            score += 60
        if role_template == "coder" and route_name in {"designer", "publisher", "copywriter"}:
            score += 25
        if role_template == "documentation_writer" and route_name in {"documentation", "commit_summary", "pr_summary"}:
            score += 25
        if role_template == "escalation" and route_name == "escalation":
            score += 25
        if role_template == "research_search" and route_name == "research_search":
            score += 25
        if str(role.get("cli", "")).strip().lower() == preferred_cli:
            score += 20
        if score > 0:
            ranked.append((score, -index, role_code))

    if not ranked:
        return ""
    ranked.sort(reverse=True)
    return ranked[0][2]


@dataclass(frozen=True)
class ResolvedAIRoute:
    """Resolved route information used by the orchestrator."""

    route_name: str
    role_code: str
    role_name: str
    cli: str
    template_keys: Tuple[str, ...]
    fallback_route: str
    description: str
    objective: str
    inputs: str
    outputs: str
    checklist: str
    skills: Tuple[str, ...]
    allowed_tools: Tuple[str, ...]


class AIRoleRouter:
    """Resolve logical AI routes into concrete role/provider metadata."""

    def __init__(self, roles_path: Path, routing_path: Path) -> None:
        self.roles_path = roles_path
        self.routing_path = routing_path

    def resolve(
        self,
        route_name: str,
        *,
        role_code_override: str = "",
        preset_id: str = "",
    ) -> ResolvedAIRoute:
        """Resolve one logical route with default fallback behavior."""

        normalized_route = _normalize_identifier(route_name)
        if not normalized_route:
            raise KeyError("route_name is required")

        payload = read_ai_role_routing_payload(self.routing_path)
        roles = _read_roles_index(self.roles_path)
        presets = _read_presets_index(self.roles_path, roles)
        return self._resolve_from_payload(
            normalized_route,
            payload,
            roles,
            presets,
            role_code_override=_normalize_identifier(role_code_override),
            preset_id=_normalize_identifier(preset_id),
            visited=set(),
        )

    def describe(self) -> Dict[str, Any]:
        """Return the resolved routing view for inspection and debugging."""

        payload = read_ai_role_routing_payload(self.routing_path)
        roles = _read_roles_index(self.roles_path)
        presets = _read_presets_index(self.roles_path, roles)
        resolved_routes: List[Dict[str, Any]] = []
        for route_name in sorted(payload.get("routes", {})):
            resolved = self._resolve_from_payload(
                route_name,
                payload,
                roles,
                presets,
                role_code_override="",
                preset_id="",
                visited=set(),
            )
            resolved_routes.append(
                {
                    "route_name": resolved.route_name,
                    "role_code": resolved.role_code,
                    "role_name": resolved.role_name,
                    "cli": resolved.cli,
                    "template_keys": list(resolved.template_keys),
                    "fallback_route": resolved.fallback_route,
                    "description": resolved.description,
                    "objective": resolved.objective,
                    "inputs": resolved.inputs,
                    "outputs": resolved.outputs,
                    "checklist": resolved.checklist,
                    "skills": list(resolved.skills),
                    "allowed_tools": list(resolved.allowed_tools),
                }
            )
        return {
            "version": payload.get("version", 1),
            "strategy": payload.get("strategy", {}),
            "routes": resolved_routes,
        }

    def _resolve_from_payload(
        self,
        route_name: str,
        payload: Dict[str, Any],
        roles: Dict[str, Dict[str, Any]],
        presets: Dict[str, Tuple[str, ...]],
        *,
        role_code_override: str,
        preset_id: str,
        visited: set[str],
    ) -> ResolvedAIRoute:
        if route_name in visited:
            raise ValueError(f"AI role routing cycle detected at route '{route_name}'")
        visited.add(route_name)

        defaults = default_ai_role_routing_payload()
        routes = payload.get("routes", {})
        route_config = dict(routes.get(route_name, defaults["routes"].get(route_name, {})))
        default_config = dict(defaults["routes"].get(route_name, {}))
        if not route_config:
            raise KeyError(f"Unknown AI role route: {route_name}")

        requested_role_code = role_code_override
        if not requested_role_code and preset_id:
            requested_role_code = _select_role_override_from_preset(
                route_name=route_name,
                preset_id=preset_id,
                roles=roles,
                presets=presets,
                route_config=route_config,
                default_config=default_config,
            )

        role_code = requested_role_code or _normalize_identifier(str(route_config.get("role_code", "")))
        resolved_role = roles.get(role_code)
        if resolved_role is None or not resolved_role.get("enabled", True):
            fallback_role_code = _normalize_identifier(str(default_config.get("role_code", "")))
            resolved_role = roles.get(fallback_role_code)
            role_code = fallback_role_code

        cli = ""
        role_name = role_code or route_name
        objective = ""
        inputs = ""
        outputs = ""
        checklist = ""
        skills: Tuple[str, ...] = ()
        allowed_tools: Tuple[str, ...] = ()
        if resolved_role:
            cli = str(resolved_role.get("cli", "")).strip().lower()
            role_name = str(resolved_role.get("name", "")).strip() or role_name
            objective = str(resolved_role.get("objective", "")).strip()
            inputs = str(resolved_role.get("inputs", "")).strip()
            outputs = str(resolved_role.get("outputs", "")).strip()
            checklist = str(resolved_role.get("checklist", "")).strip()
            skills = tuple(resolved_role.get("skills", []) or [])
            allowed_tools = tuple(resolved_role.get("allowed_tools", []) or [])

        template_keys = tuple(
            _normalize_template_keys(route_config.get("template_keys"))
            or _normalize_template_keys(default_config.get("template_keys"))
        )
        fallback_route = _normalize_identifier(str(route_config.get("fallback_route", "")))
        if not fallback_route:
            fallback_route = _normalize_identifier(str(default_config.get("fallback_route", "")))
        description = str(route_config.get("description", "")).strip() or str(
            default_config.get("description", "")
        ).strip()

        if not template_keys and fallback_route:
            return self._resolve_from_payload(
                fallback_route,
                payload,
                roles,
                presets,
                role_code_override=role_code_override,
                preset_id=preset_id,
                visited=visited,
            )
        if not template_keys:
            raise ValueError(f"AI role route '{route_name}' does not define any template keys")

        return ResolvedAIRoute(
            route_name=route_name,
            role_code=role_code or route_name,
            role_name=role_name,
            cli=cli,
            template_keys=template_keys,
            fallback_route=fallback_route,
            description=description,
            objective=objective,
            inputs=inputs,
            outputs=outputs,
            checklist=checklist,
            skills=skills,
            allowed_tools=allowed_tools,
        )
