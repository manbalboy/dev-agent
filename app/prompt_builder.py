"""Prompt and markdown content builders for orchestration stages."""

from __future__ import annotations

from pathlib import Path
import re
from textwrap import dedent
from typing import Any, Dict, List


SEARCH_TOOL_GUIDE = dedent(
    """
    정보가 불충분하거나 기술 판단이 애매하면 아래 검색 도구를 먼저 실행해 근거를 확보하세요.

    검색 도구:
    - 명령:
      `python3 /home/docker/agentHub/workspaces/main/scripts/search_research_tool.py --query "<핵심질문>" --api-key "${SEARCH_API_KEY}" --json-out "{work_dir}/_docs/SEARCH_RESULT.json" --md-out "{work_dir}/_docs/SEARCH_CONTEXT.md"`
    - 결과 파일:
      - `{work_dir}/_docs/SEARCH_RESULT.json` (원본 검색 결과)
      - `{work_dir}/_docs/SEARCH_CONTEXT.md` (요약 컨텍스트)

    검색 사용 규칙:
    - 필요한 경우에만 실행하고 과도한 반복 검색은 금지.
    - 최종 판단/계획/리뷰에 `SEARCH_CONTEXT.md` 근거를 반영.
    - 근거가 약하면 추정이라고 명시.
    """
).strip()


def build_spec_markdown(
    repository: str,
    issue_number: int,
    issue_url: str,
    issue_title: str,
    issue_body: str,
    preview_host: str = "ssh.manbalboy.com",
    preview_port_start: int = 7000,
    preview_port_end: int = 7099,
    preview_cors_origins: str = "",
) -> str:
    """Create SPEC.md content from GitHub issue details.

    SPEC is the stable contract between issue text and AI workers.
    """

    cleaned_body = _sanitize_issue_body_for_spec(issue_body)
    safe_body = cleaned_body.strip() or "(Issue body is empty.)"
    normalized_lines = _extract_request_lines(cleaned_body)
    goal = normalized_lines[0] if normalized_lines else (issue_title.strip() or "요청 목표 정의 필요")
    scope_in = normalized_lines[:5] if normalized_lines else ["이슈 원문 기준 구현 범위 정리 필요"]
    scope_out = [
        "요청 본문에 없는 신규 대기능 추가",
        "운영 안정성과 무관한 과도한 리팩토링",
        "자동 머지/자동 배포",
    ]
    acceptance = [
        "핵심 기능이 재현 가능한 단계로 동작한다.",
        "실행/테스트 방법이 문서에 명시된다.",
        "변경 범위와 비범위가 구분되어 설명된다.",
    ]
    if normalized_lines:
        acceptance[0] = f"요청 핵심: '{normalized_lines[0]}'이(가) 동작한다."
    constraints = [
        "우선순위 규칙은 SPEC.json > SPEC.md > issue 원문 순서를 따른다.",
        "변경 범위는 MVP 기준으로 최소화한다.",
        "필요 시 웹/테스트 포트는 3000번대를 사용한다.",
    ]
    cors_text = preview_cors_origins.strip() or "https://manbalboy.com, http://manbalboy.com, http://localhost"
    lines: List[str] = [
        "# SPEC",
        "",
        f"- Repository: {repository}",
        f"- Issue: #{issue_number}",
        f"- URL: {issue_url}",
        f"- Title: {issue_title}",
        "",
        "## 원본 요청",
        "",
        safe_body,
        "",
        "## 목표(Goal)",
        "",
        f"- {goal}",
        "",
        "## 범위(Scope In)",
        "",
    ]
    lines.extend(f"- {item}" for item in scope_in)
    lines.extend(
        [
            "",
            "## 비범위(Scope Out)",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in scope_out)
    lines.extend(
        [
            "",
            "## 완료 조건(Acceptance Criteria)",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in acceptance)
    lines.extend(
        [
            "",
            "## 제약 조건(Constraints)",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in constraints)
    lines.extend(
        [
            "",
            "## Rule Of Engagement",
            "",
            "- 오케스트레이터가 단계 순서와 재시도 정책을 결정합니다.",
            "- AI 도구는 컨트롤러가 아니라 작업자(worker)입니다.",
            "- 변경 범위는 MVP에 맞게 최소화합니다.",
            "- 구현 단계에서 로컬 실행 포트가 필요하면 충돌 방지를 고려합니다.",
            "",
            "## Deployment & Preview Requirements",
            "",
            "- 1회 실행 사이클의 결과물은 Docker 실행 가능 상태를 목표로 구현합니다.",
            f"- Preview 외부 노출 포트는 {preview_port_start}-{preview_port_end} 범위를 사용합니다.",
            f"- Preview 외부 기준 도메인/호스트: http://{preview_host}:{preview_port_start}",
            "- CORS 허용 대상은 manbalboy.com 계열 또는 localhost 계열로 제한합니다.",
            f"- 허용 origin 정책(기준값): {cors_text}",
            "- PR 본문에는 Docker Preview 정보(컨테이너/포트/URL)를 포함합니다.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def build_spec_json(
    repository: str,
    issue_number: int,
    issue_url: str,
    issue_title: str,
    issue_body: str,
) -> Dict[str, Any]:
    """Create machine-readable SPEC.json from issue details."""

    cleaned_body = _sanitize_issue_body_for_spec(issue_body)
    app_type = _infer_app_type(issue_title, cleaned_body)
    lines = _extract_request_lines(cleaned_body)
    goal = lines[0] if lines else (issue_title.strip() or "요청 목표 정의 필요")
    scope_in = lines[:6] if lines else ["요청 본문 기반 구현 범위 정리 필요"]
    scope_out = [
        "요청 본문에 없는 신규 대기능 추가",
        "전체 시스템 구조 재작성",
        "자동 머지/자동 배포",
    ]
    acceptance = [
        f"핵심 목표('{goal}')가 재현 가능하게 동작한다.",
        "실행/검증 방법이 문서로 제공된다.",
        "범위(in/out)와 리스크가 문서에 명시된다.",
    ]
    constraints = [
        "우선순위 규칙: SPEC.json > SPEC.md > issue 원문",
        "MVP 우선, 과도한 범위 확장 금지",
        "보안/파괴적 명령은 승인 없이 실행 금지",
    ]
    risks = [
        {"name": "요구사항 모호성", "mitigation": "모호 항목은 보완 질문으로 명확화"},
        {"name": "범위 확장", "mitigation": "scope_out 명시 및 게이트로 차단"},
    ]
    validation = {
        "test_strategy": "핵심 기능 재현 + 기본 테스트 명령 통과",
        "required_reports": ["PLAN.md", "REVIEW.md", "STATUS.md"],
    }
    artifacts = ["SPEC.md", "SPEC.json", "PLAN.md"]

    return {
        "schema_version": "1.0",
        "repository": repository,
        "app_type": app_type,
        "issue": {
            "number": issue_number,
            "url": issue_url,
            "title": issue_title,
        },
        "goal": goal,
        "scope_in": scope_in,
        "scope_out": scope_out,
        "acceptance_criteria": acceptance,
        "constraints": constraints,
        "risks": risks,
        "validation": validation,
        "artifacts": artifacts,
        "priority_order": ["SPEC.json", "SPEC.md", "issue_body"],
        "raw_request": cleaned_body.strip() or "(Issue body is empty.)",
    }


def _infer_app_type(issue_title: str, issue_body: str) -> str:
    """Infer app type for test orchestration routing."""

    text = f"{issue_title}\n{issue_body}".lower()
    if any(token in text for token in ["react native", "ios", "android", "mobile app"]):
        return "app"
    if any(token in text for token in ["cli", "command line", "터미널", "쉘", "shell script"]):
        return "cli"
    if any(token in text for token in ["api", "endpoint", "fastapi", "rest", "graphql"]):
        return "api"
    return "web"


def _extract_request_lines(issue_body: str) -> List[str]:
    """Extract concise request lines from free-form issue body."""

    body = _sanitize_issue_body_for_spec(issue_body)
    body = body.strip()
    if not body:
        return []
    lines: List[str] = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^[-*]\s+", "", stripped)
        stripped = re.sub(r"^\d+\.\s+", "", stripped)
        if stripped in {"---", "----", "```"}:
            continue
        if len(stripped) < 2:
            continue
        lines.append(stripped)
    return lines


def _sanitize_issue_body_for_spec(issue_body: str) -> str:
    """Remove metadata blocks (for orchestration) from user request body."""

    raw_lines = (issue_body or "").splitlines()
    cleaned: List[str] = []
    in_role_preset = False
    for raw in raw_lines:
        stripped = raw.strip()

        if re.match(r"^##\s*ROLE\s*PRESET\s*$", stripped, flags=re.IGNORECASE):
            in_role_preset = True
            continue
        if re.match(r"^-\s*##\s*ROLE\s*PRESET\s*$", stripped, flags=re.IGNORECASE):
            in_role_preset = True
            continue

        if in_role_preset:
            if not stripped:
                continue
            if re.match(r"^-\s*preset_id\s*:", stripped, flags=re.IGNORECASE):
                continue
            if re.match(r"^-\s*roles\s*:", stripped, flags=re.IGNORECASE):
                continue
            if stripped.startswith("## "):
                in_role_preset = False
            else:
                in_role_preset = False

        if re.match(r"^-\s*preset_id\s*:", stripped, flags=re.IGNORECASE):
            continue
        if re.match(r"^-\s*roles\s*:", stripped, flags=re.IGNORECASE):
            continue
        cleaned.append(raw)

    return "\n".join(cleaned).strip()



def build_planner_prompt(
    spec_path: str,
    plan_path: str,
    review_path: str = "",
    is_long_term: bool = False,
    is_refinement_round: bool = False,
    planning_mode: str = "general",
) -> str:
    """Prompt text for planner model (Gemini)."""

    base = dedent(
        f"""
        당신은 PLAN.md의 최종 markdown 본문을 생성합니다.

        입력 참고 자료:
        - {spec_path}
        - {review_path} (파일이 존재하고 비어있지 않으면 반드시 반영)

        출력 대상 경로(참고용):
        - {plan_path}

        필수 섹션:
        1. Task breakdown with priority
        2. MVP scope / out-of-scope
        3. Completion criteria
        4. Risks and test strategy
        5. Design intent and style direction
        6. Extensible architecture design
        7. MVP delivery phases

        Design intent and style direction 섹션 필수 항목:
        - 기획 의도: 이 기능이 사용자에게 전달해야 하는 핵심 경험/메시지
        - 디자인 풍: 예) 미니멀, 모던, 대시보드형, 카드형 등 구체 스타일
        - 시각 원칙: 컬러/패딩/마진/타이포의 방향성
        - 반응형 원칙: 모바일 우선 규칙

        Extensible architecture design 섹션 필수 항목:
        - 모듈 경계: 역할/도메인별 책임 분리
        - 인터페이스: 입력/출력 계약(SPEC.json/문서/로그)
        - 확장 포인트: 새 역할/도구/워크플로 추가 방법
        - 운영성: 실패/재시도/에스컬레이션 경로

        Technology ruleset 섹션 필수 항목:
        - 플랫폼 분류: app / web / api 중 해당 항목 명시
        - app 이면 React Native 기반으로 계획
        - web 이면 React 또는 Nuxt 기반 라이브러리/프레임워크로 계획
        - api 가 필요하면 FastAPI 기반으로 계획

        작성 규칙:
        - 반드시 한국어로 작성.
        - 문서명과 고유 명칭(예: PLAN, MVP, TODO)은 영문 유지.
        - 본문 설명은 한국어로 작성.
        - 각 섹션은 실행 가능한 체크리스트와 산출물 파일을 포함.
        - 계획 작성 전에 저장소의 관련 코드/문서/테스트를 직접 검색해 현재 상태를 파악.
        - 변경 파일 후보와 영향 범위를 근거 기반으로 명시.
        - REVIEW.md가 있으면 TODO를 고도화 플랜에 반영.
        - 실행 가이드에 포트가 필요하면 3000번대 포트만 사용.
        - markdown 본문만 출력하고, 작업 과정 설명은 금지.
        - 도구/터미널/파일 조작 과정 언급 금지.
        - 코딩 에이전트가 바로 실행 가능한 실무형 계획으로 작성.

        TOOL_REQUEST 규칙(정보 부족 시에만):
        - 최신 정보/외부 근거가 없어 계획 품질이 떨어질 때만 TOOL_REQUEST를 출력.
        - TOOL_REQUEST를 출력할 때는 PLAN 본문을 쓰지 말고 아래 포맷만 출력.
        [TOOL_REQUEST]
        tool: research_search
        query: <한 줄 검색 질의>
        reason: <왜 필요한지 한 줄>
        [/TOOL_REQUEST]
        - tool 값은 반드시 research_search만 허용.
        """
    ).strip()
    base = base + "\n\n" + SEARCH_TOOL_GUIDE

    mode = (planning_mode or "general").strip().lower()
    mode_addendum = ""
    if mode == "dev_planning":
        mode_addendum = dedent(
            """
            개발 기획 특화 규칙:
            - 개발에 필요한 도구/라이브러리/프레임워크를 후보가 아닌 확정안으로 제시하세요.
            - 각 기술요소마다 선택 이유, 대체안, 버전/호환성 주의점을 작성하세요.
            - 개발 청사진(architecture blueprint)을 포함하세요:
              모듈 경계, 데이터 흐름, API/이벤트 계약, 상태관리 전략, 에러/복구 전략.
            - 실제 작업 단위를 우선순위 백로그로 작성하세요:
              작업 ID, 설명, 선행조건, 산출물, 완료조건, 담당역할.
            - 테스트/검증 계획을 작업 단위와 1:1 매핑하세요.
            - 최종 출력은 코더가 바로 구현 가능한 실행 지시서여야 합니다.
            """
        ).strip()
    elif mode == "big_picture":
        mode_addendum = dedent(
            """
            큰틀 기획 규칙:
            - 문제 정의와 목표 지점을 명확히 하고, 구현 방향을 거시적으로 정리하세요.
            - 세부 구현보다는 범위/우선순위/리스크/마일스톤에 집중하세요.
            """
        ).strip()

    if not is_long_term:
        if not is_refinement_round:
            if mode_addendum:
                return base + "\n\n" + mode_addendum + "\n"
            return base + "\n"
        refinement_addendum = dedent(
            """
            고도화 플랜 단계 규칙(REVIEW 반영 시에만 적용):
            - REVIEW.md TODO를 우선 반영하고, 현재 구현과 자연스럽게 연결되는 인접 기능만 추가 가능합니다.
            - 톤앤매너(디자인 의도/문체/상호작용 스타일)와 일관성을 반드시 유지하세요.
            - 동떨어진 신규 기능, 도메인 이탈 기능, 과도한 범위 확장은 금지합니다.
            - 추가 기능은 최대 1~2개로 제한하고, 각 항목에 근거(왜 필요한지)와 구현 경계를 명시하세요.
            """
        ).strip()
        composed = base
        if mode_addendum:
            composed += "\n\n" + mode_addendum
        return composed + "\n\n" + refinement_addendum + "\n"

    long_term_addendum = dedent(
        """
        장기 플랜 모드 규칙([장기]):
        - 목표 기간을 최소 3개 마일스톤으로 분해하세요. (예: M1/M2/M3)
        - 각 마일스톤마다 산출물, 완료 조건, 리스크, 롤백 포인트를 작성하세요.
        - 즉시 구현 범위(MVP)와 후속 고도화 범위를 명확히 분리하세요.
        - 운영 관점(모니터링/장애 대응/확장성) 체크리스트를 추가하세요.
        """
    ).strip()
    if not is_refinement_round:
        composed = base + "\n\n" + long_term_addendum
        if mode_addendum:
            composed += "\n\n" + mode_addendum
        return composed + "\n"
    refinement_addendum = dedent(
        """
        고도화 플랜 단계 규칙(REVIEW 반영 시에만 적용):
        - REVIEW.md TODO를 우선 반영하고, 현재 구현과 자연스럽게 연결되는 인접 기능만 추가 가능합니다.
        - 톤앤매너(디자인 의도/문체/상호작용 스타일)와 일관성을 반드시 유지하세요.
        - 동떨어진 신규 기능, 도메인 이탈 기능, 과도한 범위 확장은 금지합니다.
        - 추가 기능은 최대 1~2개로 제한하고, 각 항목에 근거(왜 필요한지)와 구현 경계를 명시하세요.
        """
    ).strip()
    composed = base + "\n\n" + long_term_addendum
    if mode_addendum:
        composed += "\n\n" + mode_addendum
    return composed + "\n\n" + refinement_addendum + "\n"



def build_coder_prompt(
    plan_path: str,
    review_path: str,
    coding_goal: str,
    design_path: str = "",
    design_tokens_path: str = "",
    token_handoff_path: str = "",
    publish_handoff_path: str = "",
) -> str:
    """Prompt text for coder model (Codex)."""

    # We include both PLAN and REVIEW references in one prompt shape so the same
    # command template can be reused for implementation and fix stages.
    return dedent(
        f"""
        Goal: {coding_goal}

        {plan_path}의 지시를 따르세요.
        특히 PLAN.md의 "Design intent and style direction"을 구현에 반드시 반영하세요.
        {design_path}가 존재하고 비어있지 않으면 DESIGN_SYSTEM.md의 토큰/컴포넌트/반응형 규칙을 구현에 반영하세요.
        {design_tokens_path}가 존재하면 토큰(theme/color/typography/spacing)을 코드 변수에 매핑하세요.
        {token_handoff_path}가 존재하면 인계 체크리스트를 우선 반영하세요.
        {publish_handoff_path}가 존재하면 퍼블리셔 인계사항을 우선 반영하세요.
        {review_path}가 존재하고 비어있지 않으면 TODO 항목을 반영하세요.

        Requirements:
        - 코드 변경은 이 저장소에 직접 적용.
        - 커밋 메시지와 로그 설명은 간결하게 유지.
        - PR 자동 머지는 금지.
        - 문서 본문은 한국어 우선, 식별자/명칭은 영문 유지.
        - 사용자에게 보여지는 설명/요약/문장은 반드시 한국어로 작성.
        - 웹/테스트 실행 포트는 3100번대만 사용.
        - PLAN.md와 DESIGN_SYSTEM.md가 충돌하면 DESIGN_SYSTEM.md를 우선 적용.
        - PLAN.md의 Technology ruleset을 반드시 준수.
        - app 분류 작업은 React Native 기반으로 구현.
        - web 분류 작업은 React 또는 Nuxt 기반으로 구현.
        - api 구현이 필요하면 FastAPI를 사용.
        - DESIGN_SYSTEM.md에 명시된 WOW Point 1개를 반드시 구현.
        - 회귀(regression) 유발 금지.
        - 보안 민감정보 하드코딩 금지.
        - 실패 시 우회가 아닌 원인 기반 수정 우선.

        개발 체크리스트:
        1. 요구사항 충족: SPEC/PLAN 범위를 벗어나지 않았는가?
        2. UI 일관성: 라이트/다크, 반응형, 접근성이 유지되는가?
        3. 안정성: 예외/에러/빈 상태 처리가 포함되는가?
        4. 검증성: 변경 사항을 확인할 테스트/실행 방법이 있는가?
        5. 유지보수성: 최소 변경으로 명확한 구조를 유지했는가?
        """
    ).strip() + "\n\n" + SEARCH_TOOL_GUIDE + "\n"



def build_designer_prompt(spec_path: str, plan_path: str, design_path: str) -> str:
    """Prompt text for designer model (Codex) to produce DESIGN_SYSTEM.md."""

    token_path = str(Path(design_path).with_name("DESIGN_TOKENS.json"))
    handoff_path = str(Path(design_path).with_name("TOKEN_HANDOFF.md"))
    return dedent(
        f"""
        당신은 디자인 시스템 기획 담당이며, 아래 3개 산출물을 작성합니다.

        참고 자료:
        - Spec: {spec_path}
        - Plan: {plan_path}
        - PLAN.md의 "Design intent and style direction" 섹션을 기준으로 디자인 시스템을 정의하세요.

        출력 대상 경로(참고용):
        - {design_path}
        - {token_path}
        - {handoff_path}

        필수 설계 범위:
        1. Information hierarchy (가독성 중심 레이아웃)
        2. Color system (배경/텍스트/상태 색상 토큰)
        3. Spacing scale (padding/margin 기준)
        4. Typography scale (폰트 크기/굵기/행간)
        5. Responsive rules (모바일 웹 우선 규칙)
        6. Component guidance (카드/버튼/폼/테이블 최소 규칙)
        7. Plan alignment (기획 의도/디자인 풍과 어떻게 정합되는지)
        8. WOW Point (유저를 끌어당기는 핵심 디자인 요소 1개)

        WOW Point 작성 규칙:
        - 반드시 1개만 제안.
        - 실제 구현 가능한 UI 요소로 작성.
        - 유도하려는 사용자 감정과 성공 기준을 함께 명시.

        작성 규칙:
        - 반드시 한국어로 작성.
        - 문서명과 고유 명칭(예: DESIGN_SYSTEM, token, component)은 영문 유지.
        - DESIGN_SYSTEM.md는 markdown 본문으로 작성.
        - DESIGN_TOKENS.json은 JSON 객체 형식으로 작성.
        - TOKEN_HANDOFF.md에는 개발자가 바로 적용할 파일/순서/검증 체크리스트를 작성.
        - 도구/터미널/파일 조작 과정 언급 금지.
        """
    ).strip() + "\n\n" + SEARCH_TOOL_GUIDE + "\n"


def build_publisher_prompt(
    *,
    spec_path: str,
    plan_path: str,
    design_path: str,
    publish_checklist_path: str,
    publish_handoff_path: str,
) -> str:
    """Prompt text for publisher step (Codex) to apply design plan and handoff docs."""

    return dedent(
        f"""
        Goal: 퍼블리싱 단계 수행(디자인 시스템을 코드로 반영) + 개발자 전달 문서 작성

        참고 자료:
        - Spec: {spec_path}
        - Plan: {plan_path}
        - Design system: {design_path}

        필수 작업:
        1. DESIGN_SYSTEM.md 기준으로 UI/스타일/컴포넌트 구조를 코드에 반영
        2. 라이트/다크 모드 모두 동작하도록 토큰 연결
        3. 접근성 기본(포커스, 대비, 시맨틱) 반영
        4. 아래 문서를 반드시 생성
           - {publish_checklist_path}
           - {publish_handoff_path}

        문서 작성 규칙:
        - PUBLISH_CHECKLIST.md: 적용 완료 항목/미완료 항목을 체크리스트로 작성
        - PUBLISH_HANDOFF.md: 개발자가 이어받을 파일 목록, 남은 작업, 테스트 방법 작성

        출력 규칙:
        - 작업 과정 설명 금지
        - 불필요한 장문 금지
        - 실제 변경 가능한 파일만 다룰 것
        """
    ).strip() + "\n\n" + SEARCH_TOOL_GUIDE + "\n"


def build_pr_summary_prompt(
    spec_path: str,
    plan_path: str,
    review_path: str,
    design_path: str,
    issue_title: str,
    issue_number: int,
    is_long_term: bool = False,
) -> str:
    """Prompt text for Claude PR summary generation."""

    base = dedent(
        f"""
        당신은 PR 본문(markdown)을 최종 정리합니다.

        참고 자료:
        - Spec: {spec_path}
        - Plan: {plan_path}
        - Review: {review_path}
        - Design system: {design_path}

        이슈 정보:
        - Title: {issue_title}
        - Number: #{issue_number}

        출력 규칙:
        - markdown 본문만 출력.
        - 한국어로 작성.
        - 아래 섹션을 포함:
          1) Summary
          2) What Changed
          3) Test Results
          4) Risks / Follow-ups
          5) Closes #{issue_number}
        """
    ).strip()

    if not is_long_term:
        return base + "\n"

    long_term_addendum = dedent(
        """
        장기 플랜 모드([장기]) 추가 규칙:
        - 마일스톤 기준으로 변경사항을 묶어 요약하세요.
        - 이번 PR 범위와 다음 단계 범위를 명확히 분리하세요.
        """
    ).strip()
    return base + "\n\n" + long_term_addendum + "\n"


def build_commit_message_prompt(
    spec_path: str,
    plan_path: str,
    review_path: str,
    design_path: str,
    stage_name: str,
    commit_type: str,
) -> str:
    """Prompt text for Claude commit message summary generation."""

    return dedent(
        f"""
        당신은 Git 커밋 메시지 요약문 1줄만 생성합니다.

        참고 자료:
        - Spec: {spec_path}
        - Plan: {plan_path}
        - Review: {review_path}
        - Design system: {design_path}

        현재 커밋 컨텍스트:
        - Stage: {stage_name}
        - Commit type: {commit_type}

        출력 규칙:
        - 반드시 한국어 1줄만 출력
        - 불필요한 설명/마크다운/코드블록 금지
        - 60자 내외의 간결한 구현 요약
        - type 접두사(feat:, fix:)는 포함하지 말 것
        """
    ).strip() + "\n"


def build_reviewer_prompt(spec_path: str, plan_path: str, review_path: str) -> str:
    """Prompt text for reviewer model (Gemini)."""

    return dedent(
        f"""
        당신은 REVIEW.md의 최종 markdown 본문을 생성합니다.

        아래 자료를 기반으로 현재 저장소 상태를 리뷰하세요:
        - Spec: {spec_path}
        - Plan: {plan_path}

        출력 대상 경로(참고용):
        - {review_path}

        필수 리뷰 카테고리:
        - Functional bugs
        - Security concerns
        - Missing tests / weak test coverage
        - Edge cases

        마지막에는 coder를 위한 TODO checklist를 추가하세요.

        작성 규칙:
        - 반드시 한국어로 작성.
        - 문서명과 카테고리 명칭(예: REVIEW, TODO, Functional bugs)은 영문 유지.
        - 상세 설명은 한국어로 작성.
        - 실행/재현 예시에서 포트가 나오면 3100번대만 사용.
        - markdown 본문만 출력하고 작업 과정/내부 추론/메타 코멘트 금지.
        - 출력 내 후속 질문 금지.
        """
    ).strip() + "\n"



def build_status_markdown(last_error: str, next_actions: list[str]) -> str:
    """Create STATUS.md when final failure occurs."""

    actions_block = "\n".join(f"- {item}" for item in next_actions)
    return dedent(
        f"""
        # STATUS

        ## 남은 문제
        - {last_error}

        ## 재현 방법
        - Job 로그에서 실패한 명령을 확인하고 저장소 루트에서 재실행하세요.

        ## Next Actions
        {actions_block}
        """
    ).strip() + "\n"
