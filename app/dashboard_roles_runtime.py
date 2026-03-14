"""Role/preset runtime helpers for dashboard admin APIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def normalize_role_code(value: str) -> str:
    """Normalize one role/preset identifier."""

    lowered = (value or "").strip().lower()
    filtered = "".join(ch for ch in lowered if ch.isalnum() or ch in {"-", "_"})
    return filtered[:40]


def normalize_role_tag_list(values: Any) -> List[str]:
    """Normalize role skill/tool metadata into stable identifiers."""

    items: List[str] = []
    if isinstance(values, str):
        items = [part.strip() for part in values.replace("\n", ",").split(",")]
    elif isinstance(values, list):
        items = [str(item).strip() for item in values]
    else:
        return []

    normalized: List[str] = []
    for item in items:
        token = normalize_role_code(item)[:80]
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def default_roles_payload() -> Dict[str, Any]:
    """Default role catalog for role-management MVP."""

    role_rows = [
        ("ai-helper", "AI 도우미", "codex", "codex_helper", "요청/문제 정리", "분석/조치안"),
        ("log-analyzer-codex", "로그 분석 도우미(Codex)", "codex", "coder", "워크플로우 로그", "문제점/조치안"),
        ("log-analyzer-gemini", "로그 분석 도우미(Gemini)", "gemini", "reviewer", "워크플로우 로그", "문제점/조치안"),
        ("coder", "코더", "codex", "coder", "SPEC/PLAN", "코드 변경"),
        ("designer", "디자이너", "codex", "coder", "요구사항", "UI/디자인 산출물"),
        ("tester", "테스터", "bash", "", "코드 상태", "테스트 결과"),
        ("reviewer", "리뷰어", "gemini", "reviewer", "코드 diff", "리뷰 리포트"),
        ("copywriter", "카피라이터", "codex", "coder", "기획의도/디자인/퍼블리싱 결과", "COPYWRITING_PLAN.md, COPY_DECK.md"),
        ("consultant", "컨설턴트", "gemini", "planner", "현황", "전략 제안"),
        ("qa", "QA", "bash", "", "테스트 계획", "품질 점검"),
        ("architect", "플래너", "gemini", "planner", "요구사항", "실행 계획"),
        ("devops-sre", "인프라·운영 엔지니어", "bash", "", "서비스 상태", "운영 조치"),
        ("escalation-helper", "에스컬레이션 도우미", "codex", "escalation", "실패 로그/상태", "보조 분석/다음 액션"),
        ("summary-reviewer", "요약 리뷰어", "gemini", "reviewer", "변경 요약/실패 맥락", "커밋/PR/에스컬레이션 요약"),
        ("security", "보안 엔지니어", "bash", "", "코드/설정", "보안 점검"),
        ("db-engineer", "데이터베이스 엔지니어", "bash", "", "스키마", "DB 변경안"),
        ("performance", "성능 최적화 엔지니어", "bash", "", "프로파일링", "개선안"),
        ("accessibility", "접근성 전문가", "bash", "", "UI", "접근성 점검"),
        ("test-automation", "테스트 자동화 엔지니어", "bash", "", "테스트 전략", "자동화 코드"),
        ("release-manager", "배포 관리자", "bash", "", "릴리즈 계획", "배포 체크"),
        ("incident-analyst", "장애 원인 분석가", "codex", "codex_helper", "로그/지표", "RCA"),
        ("orchestration-helper", "오케스트레이션 도우미", "codex", "codex_helper", "워크플로우 상태/로그", "다음 단계/재시도 전략"),
        ("system-owner", "시스템 오너", "gemini", "planner", "이슈 본문/SPEC.md", "확정 스펙/우선순위"),
        ("tech-writer", "기술 문서 작성가", "codex", "documentation_writer", "SPEC/PLAN/REVIEW", "README.md, COPYRIGHT.md, DEVELOPMENT_GUIDE.md"),
        ("test-reviewer", "테스트 리뷰어", "gemini", "reviewer", "테스트 리포트/실행 로그", "테스트 결과 해석/품질 게이트 판단"),
        ("product-analyst", "제품 분석가", "gemini", "planner", "지표/요구", "개선 우선순위"),
        ("publisher", "퍼블리셔", "codex", "coder", "디자인 시스템/화면 구조", "퍼블리싱 결과물"),
        ("research-agent", "정보검색 도우미", "python3", "research_search", "질문/키워드", "SEARCH_CONTEXT.md"),
        ("refactor-specialist", "리팩토링 전문가", "codex", "coder", "코드베이스", "구조 개선"),
        ("requirements-manager", "요구사항 관리자", "gemini", "planner", "이해관계자 요청", "명세"),
        ("data-ai-engineer", "데이터/AI 엔지니어", "codex", "codex_helper", "데이터 과제", "파이프라인/모델 개선"),
    ]
    roles = [
        {
            "code": code,
            "name": name,
            "objective": "",
            "cli": cli,
            "template_key": template_key,
            "inputs": inputs,
            "outputs": outputs,
            "checklist": "",
            "skills": [],
            "allowed_tools": [],
            "enabled": True,
        }
        for code, name, cli, template_key, inputs, outputs in role_rows
    ]
    tool_defaults = {
        "ai-helper": ["log_lookup", "repo_search", "memory_search"],
        "data-ai-engineer": ["log_lookup", "repo_search", "memory_search"],
        "incident-analyst": ["log_lookup", "repo_search", "memory_search"],
        "orchestration-helper": ["log_lookup", "repo_search", "memory_search"],
    }
    checklist_defaults = {
        "summary-reviewer": "Gemini로 커밋/PR 메시지와 실패 요약을 판단·정리",
        "tech-writer": "Codex로 문서 번들 실제 작성",
        "test-reviewer": "Gemini로 테스트 실패 원인, 재현 경로, 품질 게이트 판단 정리",
    }
    for role in roles:
        role["allowed_tools"] = list(tool_defaults.get(role["code"], []))
        role["checklist"] = checklist_defaults.get(role["code"], "")
    presets = [
        {
            "preset_id": "default-dev",
            "name": "기본 개발",
            "description": "설계-구현-테스트-리뷰",
            "role_codes": ["architect", "coder", "tester", "reviewer"],
        },
        {
            "preset_id": "fast-fix",
            "name": "빠른 수정",
            "description": "원인 파악 후 신속 수정",
            "role_codes": ["incident-analyst", "coder", "tester"],
        },
        {
            "preset_id": "research-first",
            "name": "근거 우선",
            "description": "검색 근거 확보 후 설계/구현",
            "role_codes": ["research-agent", "architect", "coder", "reviewer"],
        },
    ]
    return {"roles": roles, "presets": presets}


def read_roles_payload(path: Path) -> Dict[str, Any]:
    """Read role/preset payload with safe defaults."""

    defaults = default_roles_payload()
    if not path.exists():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaults
    if not isinstance(payload, dict):
        return defaults

    roles: List[Dict[str, Any]] = []
    for item in payload.get("roles", []):
        if not isinstance(item, dict):
            continue
        code = normalize_role_code(str(item.get("code", "")))
        name = str(item.get("name", "")).strip()
        if not code or not name:
            continue
        roles.append(
            {
                "code": code,
                "name": name,
                "objective": str(item.get("objective", "")).strip(),
                "cli": str(item.get("cli", "")).strip().lower(),
                "template_key": str(item.get("template_key", "")).strip(),
                "inputs": str(item.get("inputs", "")).strip(),
                "outputs": str(item.get("outputs", "")).strip(),
                "checklist": str(item.get("checklist", "")).strip(),
                "skills": normalize_role_tag_list(item.get("skills")),
                "allowed_tools": normalize_role_tag_list(item.get("allowed_tools")),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    if not roles:
        roles = defaults["roles"]

    known_codes = {str(item.get("code", "")) for item in roles}
    presets: List[Dict[str, Any]] = []
    for item in payload.get("presets", []):
        if not isinstance(item, dict):
            continue
        preset_id = normalize_role_code(str(item.get("preset_id", "")))
        name = str(item.get("name", "")).strip()
        if not preset_id or not name:
            continue
        role_codes = []
        for raw in item.get("role_codes", []):
            code = normalize_role_code(str(raw))
            if code and code in known_codes and code not in role_codes:
                role_codes.append(code)
        presets.append(
            {
                "preset_id": preset_id,
                "name": name,
                "description": str(item.get("description", "")).strip(),
                "role_codes": role_codes,
            }
        )
    if not presets:
        presets = defaults["presets"]

    roles.sort(key=lambda one: str(one.get("code", "")))
    presets.sort(key=lambda one: str(one.get("preset_id", "")))
    return {"roles": roles, "presets": presets}


def write_roles_payload(path: Path, payload: Dict[str, Any]) -> None:
    """Persist role/preset payload as pretty JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class DashboardRolesRuntime:
    """Encapsulate dashboard role/preset CRUD behavior."""

    @staticmethod
    def list_roles(*, roles_config_path: Path) -> Dict[str, Any]:
        return read_roles_payload(roles_config_path)

    @staticmethod
    def upsert_role(*, roles_config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
        role_code = normalize_role_code(str(payload.get("code", "")))
        if not role_code:
            raise ValueError("역할 코드는 영문/숫자/-/_ 형식이어야 합니다.")

        role = {
            "code": role_code,
            "name": str(payload.get("name", "")).strip(),
            "objective": str(payload.get("objective", "")).strip(),
            "cli": str(payload.get("cli", "")).strip().lower(),
            "template_key": str(payload.get("template_key", "")).strip(),
            "inputs": str(payload.get("inputs", "")).strip(),
            "outputs": str(payload.get("outputs", "")).strip(),
            "checklist": str(payload.get("checklist", "")).strip(),
            "skills": normalize_role_tag_list(payload.get("skills")),
            "allowed_tools": normalize_role_tag_list(payload.get("allowed_tools")),
            "enabled": bool(payload.get("enabled", True)),
        }
        if not role["name"]:
            raise ValueError("역할 이름은 필수입니다.")

        data = read_roles_payload(roles_config_path)
        roles = data.get("roles", [])
        replaced = False
        updated: List[Dict[str, Any]] = []
        for item in roles:
            if not isinstance(item, dict):
                continue
            code = normalize_role_code(str(item.get("code", "")))
            if not code:
                continue
            if code == role_code:
                updated.append(role)
                replaced = True
                continue
            copied = dict(item)
            copied["code"] = code
            updated.append(copied)

        if not replaced:
            updated.append(role)

        updated.sort(key=lambda item: str(item.get("code", "")))
        data["roles"] = updated
        write_roles_payload(roles_config_path, data)
        return {"saved": True, "roles": data["roles"], "presets": data.get("presets", [])}

    @staticmethod
    def delete_role(*, roles_config_path: Path, role_code: str) -> Dict[str, Any]:
        code = normalize_role_code(role_code)
        if not code:
            raise ValueError("유효하지 않은 역할 코드입니다.")

        data = read_roles_payload(roles_config_path)
        roles = [item for item in data.get("roles", []) if normalize_role_code(str(item.get("code", ""))) != code]
        data["roles"] = roles

        presets: List[Dict[str, Any]] = []
        for preset in data.get("presets", []):
            if not isinstance(preset, dict):
                continue
            copied = dict(preset)
            role_codes = [rc for rc in copied.get("role_codes", []) if normalize_role_code(str(rc)) != code]
            copied["role_codes"] = role_codes
            presets.append(copied)
        data["presets"] = presets
        write_roles_payload(roles_config_path, data)
        return {"deleted": True, "roles": roles, "presets": presets}

    @staticmethod
    def upsert_role_preset(*, roles_config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
        preset_id = normalize_role_code(str(payload.get("preset_id", "")))
        if not preset_id:
            raise ValueError("프리셋 ID는 영문/숫자/-/_ 형식이어야 합니다.")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("프리셋 이름은 필수입니다.")

        data = read_roles_payload(roles_config_path)
        known_roles = {
            normalize_role_code(str(item.get("code", "")))
            for item in data.get("roles", [])
            if isinstance(item, dict)
        }
        role_codes: List[str] = []
        for raw in payload.get("role_codes", []):
            code = normalize_role_code(str(raw))
            if code and code in known_roles and code not in role_codes:
                role_codes.append(code)

        preset = {
            "preset_id": preset_id,
            "name": name,
            "description": str(payload.get("description", "")).strip(),
            "role_codes": role_codes,
        }

        replaced = False
        updated: List[Dict[str, Any]] = []
        for item in data.get("presets", []):
            if not isinstance(item, dict):
                continue
            if normalize_role_code(str(item.get("preset_id", ""))) == preset_id:
                updated.append(preset)
                replaced = True
                continue
            copied = dict(item)
            copied["preset_id"] = normalize_role_code(str(copied.get("preset_id", "")))
            copied["role_codes"] = [
                rc
                for rc in [normalize_role_code(str(value)) for value in copied.get("role_codes", [])]
                if rc
            ]
            updated.append(copied)
        if not replaced:
            updated.append(preset)
        updated.sort(key=lambda item: str(item.get("preset_id", "")))

        data["presets"] = updated
        write_roles_payload(roles_config_path, data)
        return {"saved": True, "roles": data.get("roles", []), "presets": updated}

    @staticmethod
    def delete_role_preset(*, roles_config_path: Path, preset_id: str) -> Dict[str, Any]:
        normalized = normalize_role_code(preset_id)
        if not normalized:
            raise ValueError("유효하지 않은 프리셋 ID입니다.")
        data = read_roles_payload(roles_config_path)
        presets = [
            item
            for item in data.get("presets", [])
            if normalize_role_code(str(item.get("preset_id", ""))) != normalized
        ]
        data["presets"] = presets
        write_roles_payload(roles_config_path, data)
        return {"deleted": True, "roles": data.get("roles", []), "presets": presets}
