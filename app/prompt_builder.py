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


OPERATING_PRINCIPLES_BRIEF = dedent(
    """
    핵심 운영 원칙:
    - 완성형을 한 번에 만들지 말고 항상 MVP부터 시작한다.
    - 코드 생성보다 제품 정의, 사용자 흐름, MVP 범위, 아키텍처 설계를 우선한다.
    - 이번 라운드는 작은 단위 변경에 집중한다. 한 번에 큰 범위를 구현하지 않는다.
    - 결과는 반드시 품질 기준으로 평가 가능해야 하며 판단 근거를 문서에 남긴다.
    - 같은 문제를 반복 수정하지 말고, 품질이 오르지 않으면 전략을 바꾼다.
    - 실행 성공만이 아니라 사용성, UX 명확성, 테스트, 에러/빈/로딩 상태까지 제품 품질로 판단한다.
    """
).strip()


OPERATING_ENFORCEMENT_BRIEF = dedent(
    """
    강제 규칙:
    - 제품 정의 문서와 범위가 불충분하면 추정 구현을 시작하지 않는다.
    - MVP 범위 밖 신규 기능 확장이나 과도한 구조 재작성은 금지한다.
    - 왜 이 설계/범위/개선을 선택했는지 문서에 설명 가능해야 한다.
    """
).strip()


PROMPT_CONTEXT_CHAR_LIMIT = 12000


def _read_prompt_context(path_str: str, *, label: str) -> str:
    """Embed source file content into prompts so external CLIs can use it directly."""

    raw_path = str(path_str or "").strip()
    if not raw_path:
        return f"### {label}\n(경로 없음)\n"
    path = Path(raw_path)
    if not path.exists():
        return f"### {label}\nPath: {raw_path}\n(파일이 아직 생성되지 않음)\n"

    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as error:
        return f"### {label}\nPath: {raw_path}\n(파일 읽기 실패: {error})\n"

    if not text:
        text = "(빈 파일)"
    if len(text) > PROMPT_CONTEXT_CHAR_LIMIT:
        text = text[:PROMPT_CONTEXT_CHAR_LIMIT].rstrip() + "\n...[truncated]"

    suffix = path.suffix.lower()
    code_fence = "json" if suffix == ".json" else "md"
    return dedent(
        f"""
        ### {label}
        Path: {raw_path}
        ```{code_fence}
        {text}
        ```
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


def build_product_brief_prompt(
    spec_path: str,
    product_brief_path: str,
    *,
    job_id: str = "",
    issue_title: str = "",
    retry_feedback: str = "",
) -> str:
    """Prompt for AI to generate PRODUCT_BRIEF.md from spec context."""

    prompt = dedent(
        f"""
        당신은 제품 정의 전문가입니다. PRODUCT_BRIEF.md 전체를 한국어로 작성하세요.

        입력 참고 자료:
        - {spec_path}  (SPEC.md — 이슈 원문, 목표, 범위)
        
        아래에 입력 파일 내용이 인라인으로 포함되어 있습니다. 경로만 보지 말고 실제 내용 기준으로 작성하세요.
        {_read_prompt_context(spec_path, label="SPEC.md Content")}

        출력 대상 경로(참고용):
        - {product_brief_path}

        필수 섹션:
        0. Context Anchor — 아래 두 줄을 정확히 포함
           - Job ID: {job_id}
           - Issue Title: {issue_title}
        1. Product Goal — 이 제품이 해결하는 핵심 문제 한 문장
        2. Problem Statement — 현재 사용자가 겪는 고통 포인트
        3. Target Users — 1차 사용자와 2차 사용자 구분
        4. Core Value — 경쟁 대안 대비 차별 가치
        5. Scope Inputs — 이번 버전에 포함될 핵심 기능 (SPEC의 scope_in 반영)
        6. Success Metrics — 이 제품이 성공했다고 판단하는 정량/정성 기준
        7. Non-Goals — 이 제품에서 의도적으로 제외하는 것

        작성 규칙:
        - 반드시 한국어로 작성 (섹션 제목 영문 유지).
        - 추상적 표현 금지. 각 항목은 검증 가능한 구체 문장으로 작성.
        - 저장소의 기존 코드/README를 검색해 현재 제품 상태를 파악한 뒤 작성.
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        - markdown 본문만 출력. 작업 과정 설명, 메타 코멘트 금지.
        """
    ).strip()
    if retry_feedback.strip():
        prompt += "\n\n" + dedent(
            f"""
            이전 출력 보정 지시:
            {retry_feedback.strip()}
            - 누락된 섹션과 Context Anchor를 모두 보완해 전체 문서를 다시 작성하세요.
            - 기존 generic 문장을 반복하지 말고 SPEC.md의 실제 표현을 반영하세요.
            """
        ).strip()
    return prompt + "\n"


def build_user_flows_prompt(
    product_brief_path: str,
    user_flows_path: str,
    *,
    job_id: str = "",
    issue_title: str = "",
    retry_feedback: str = "",
) -> str:
    """Prompt for AI to generate USER_FLOWS.md from product brief."""

    prompt = dedent(
        f"""
        당신은 UX 설계 전문가입니다. USER_FLOWS.md 전체를 한국어로 작성하세요.

        입력 참고 자료:
        - {product_brief_path}  (PRODUCT_BRIEF.md — 제품 목표, 사용자, 가치)
        
        아래에 입력 파일 내용이 인라인으로 포함되어 있습니다. 실제 내용을 기준으로 사용자 흐름을 정의하세요.
        {_read_prompt_context(product_brief_path, label="PRODUCT_BRIEF.md Content")}

        출력 대상 경로(참고용):
        - {user_flows_path}

        필수 섹션:
        0. Context Anchor — 아래 두 줄을 정확히 포함
           - Job ID: {job_id}
           - Issue Title: {issue_title}
        1. Primary Flow — 핵심 사용자 여정 (단계별 번호 목록, 최소 5단계)
        2. Secondary Flows — 부수적 흐름 (설정, 오류 복구, 엣지케이스 등)
        3. UX State Checklist — 각 화면/기능에 대해 아래 3가지 상태를 명시:
           - Loading 상태: 어떤 스피너/스켈레톤/진행 메시지가 필요한가
           - Empty 상태: 데이터 없을 때 사용자에게 무엇을 보여줘야 하는가
           - Error 상태: 실패 원인과 복구 액션을 어떻게 안내하는가
        4. Entry/Exit Points — 각 흐름의 진입 조건과 종료 조건

        작성 규칙:
        - 반드시 한국어로 작성 (섹션 제목 영문 유지).
        - 각 단계는 사용자 행동(User Action)과 시스템 반응(System Response)을 구분.
        - 저장소의 기존 UI 코드/컴포넌트를 검색해 현실에 맞게 작성.
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        - markdown 본문만 출력. 작업 과정 설명 금지.
        """
    ).strip()
    if retry_feedback.strip():
        prompt += "\n\n" + dedent(
            f"""
            이전 출력 보정 지시:
            {retry_feedback.strip()}
            - 제품 목표/사용자/핵심 가치와 직접 연결되는 사용자 흐름으로 다시 작성하세요.
            - Loading / Empty / Error 상태를 빠뜨리지 마세요.
            """
        ).strip()
    return prompt + "\n"


def build_mvp_scope_prompt(
    product_brief_path: str,
    user_flows_path: str,
    spec_json_path: str,
    mvp_scope_path: str,
    *,
    job_id: str = "",
    issue_title: str = "",
    retry_feedback: str = "",
) -> str:
    """Prompt for AI to generate MVP_SCOPE.md."""

    prompt = dedent(
        f"""
        당신은 제품 범위 결정 전문가입니다. MVP_SCOPE.md 전체를 한국어로 작성하세요.

        입력 참고 자료:
        - {product_brief_path}  (PRODUCT_BRIEF.md)
        - {user_flows_path}     (USER_FLOWS.md)
        - {spec_json_path}      (SPEC.json — scope_in / scope_out)
        
        아래에 입력 파일 내용이 인라인으로 포함되어 있습니다. 실제 범위와 흐름을 기준으로 결정하세요.
        {_read_prompt_context(product_brief_path, label="PRODUCT_BRIEF.md Content")}
        {_read_prompt_context(user_flows_path, label="USER_FLOWS.md Content")}
        {_read_prompt_context(spec_json_path, label="SPEC.json Content")}

        출력 대상 경로(참고용):
        - {mvp_scope_path}

        필수 섹션:
        0. Context Anchor — 아래 두 줄을 정확히 포함
           - Job ID: {job_id}
           - Issue Title: {issue_title}
        1. In Scope — 이번 MVP에 반드시 포함되는 기능 목록 (우선순위 표기)
        2. Out of Scope — 의도적으로 제외한 기능과 제외 이유
        3. MVP Acceptance Gates — MVP가 완료되었다고 판단하는 최소 조건 (최소 3개)
        4. Post-MVP Candidates — MVP 이후 개선 루프에서 다룰 후보 기능
        5. Scope Decision Rationale — 범위 결정의 근거 (리소스, 리스크, 사용자 우선순위)

        작성 규칙:
        - 반드시 한국어로 작성 (섹션 제목 영문 유지).
        - "In Scope" 각 항목에는 우선순위(P1/P2)와 완료 조건을 함께 작성.
        - MVP Acceptance Gates는 재현 가능하고 검증 가능한 조건이어야 함.
        - 저장소 현황을 검색해 이미 구현된 기능은 In Scope에서 제외하거나 개선 범위로 표기.
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        - markdown 본문만 출력. 작업 과정 설명 금지.
        """
    ).strip()
    if retry_feedback.strip():
        prompt += "\n\n" + dedent(
            f"""
            이전 출력 보정 지시:
            {retry_feedback.strip()}
            - PRODUCT_BRIEF / USER_FLOWS / SPEC.json 사이의 공통 범위만 남기고 generic 범위는 제거하세요.
            - Acceptance Gates는 검증 가능 문장으로 다시 작성하세요.
            """
        ).strip()
    return prompt + "\n"


def build_architecture_plan_prompt(
    mvp_scope_path: str,
    user_flows_path: str,
    architecture_plan_path: str,
    *,
    job_id: str = "",
    issue_title: str = "",
    retry_feedback: str = "",
) -> str:
    """Prompt for AI to generate ARCHITECTURE_PLAN.md."""

    prompt = dedent(
        f"""
        당신은 소프트웨어 아키텍트입니다. ARCHITECTURE_PLAN.md 전체를 한국어로 작성하세요.

        입력 참고 자료:
        - {mvp_scope_path}   (MVP_SCOPE.md — 구현 범위)
        - {user_flows_path}  (USER_FLOWS.md — 사용자 흐름)
        
        아래에 입력 파일 내용이 인라인으로 포함되어 있습니다. 실제 MVP 범위와 사용자 흐름을 기준으로 설계하세요.
        {_read_prompt_context(mvp_scope_path, label="MVP_SCOPE.md Content")}
        {_read_prompt_context(user_flows_path, label="USER_FLOWS.md Content")}

        출력 대상 경로(참고용):
        - {architecture_plan_path}

        필수 섹션:
        0. Context Anchor — 아래 두 줄을 정확히 포함
           - Job ID: {job_id}
           - Issue Title: {issue_title}
        1. Layer Structure — 제품 레이어 구성 (Presentation / Application / Data / Infrastructure)
        2. Component Boundaries — 각 컴포넌트의 책임과 경계 (무엇을 하고, 무엇을 하지 않는가)
        3. Data Contracts — 단계 간 데이터 전달 방식 (파일/JSON/API 스키마 요약)
        4. Quality Gates — 구현 진입/완료 조건 (어떤 산출물이 없으면 다음 단계 진행 불가)
        5. Loop Safety Rules — 무한 개선 루프 방지 규칙:
           - 동일 문제 반복 제한 조건
           - 품질 점수 정체 감지 기준
           - 품질 하락 감지 기준
           - 전략 변경 트리거 조건
           - 복구 후보(git rollback) 정책
        6. Technology Decisions — 기술 스택 결정과 선택 이유
        7. Extension Points — 새 단계/도구/에이전트를 추가하는 방법

        작성 규칙:
        - 반드시 한국어로 작성 (섹션 제목 영문 유지).
        - 저장소의 기존 코드 구조를 검색해 현실 아키텍처에 맞게 작성.
        - 품질 게이트는 정량적 기준(점수 임계값, 반복 횟수 등)으로 명시.
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        - 특히 같은 문제 3회 이상 반복 시 전략 변경 조건을 반드시 명시한다.
        - markdown 본문만 출력. 작업 과정 설명 금지.
        """
    ).strip()
    if retry_feedback.strip():
        prompt += "\n\n" + dedent(
            f"""
            이전 출력 보정 지시:
            {retry_feedback.strip()}
            - MVP 범위 밖 확장 제안은 제거하고, 품질 게이트와 루프 안전 규칙을 더 구체화하세요.
            - generic architecture 설명이 아니라 현재 제품 정의 단계 산출물과 직접 연결하세요.
            """
        ).strip()
    return prompt + "\n"


def build_project_scaffolding_prompt(
    architecture_plan_path: str,
    mvp_scope_path: str,
    spec_json_path: str,
    bootstrap_report_path: str,
    scaffold_plan_path: str,
) -> str:
    """Prompt for AI to generate SCAFFOLD_PLAN.md."""

    return dedent(
        f"""
        당신은 프로젝트 부트스트랩 설계 담당입니다. SCAFFOLD_PLAN.md 전체를 한국어로 작성하세요.

        입력 참고 자료:
        - {architecture_plan_path}  (ARCHITECTURE_PLAN.md — 레이어/품질게이트)
        - {mvp_scope_path}          (MVP_SCOPE.md — 이번 라운드 구현 범위)
        - {spec_json_path}          (SPEC.json — 앱 유형, 제약 조건)
        - {bootstrap_report_path}   (BOOTSTRAP_REPORT.json — 현재 레포 상태/탐지 결과)

        출력 대상 경로(참고용):
        - {scaffold_plan_path}

        필수 섹션:
        1. Repository State — 현재 레포가 greenfield / partial / existing 중 어디에 해당하는가
        2. Bootstrap Mode — 이번 단계가 create / extend / stabilize 중 무엇을 해야 하는가
        3. Target Structure — 생성/정리해야 할 주요 디렉토리와 파일
        4. Required Setup Commands — 실제 부트스트랩에 필요한 초기 명령
        5. App Skeleton Contracts — entrypoint, config, test, docs 기본 계약
        6. Verification Checklist — scaffold 완료를 확인하는 최소 체크리스트
        7. Risks And Deferrals — 지금 미루는 항목과 이유

        작성 규칙:
        - 반드시 한국어로 작성 (섹션 제목 영문 유지).
        - 현재 저장소 구조를 검색해 이미 존재하는 파일/디렉토리는 재생성 대상으로 쓰지 말 것.
        - 과도한 대규모 재구성 금지. MVP를 빠르게 시작하기 위한 최소 scaffold만 제안.
        - Required Setup Commands는 실행 가능한 짧은 명령 형태로 작성.
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        - markdown 본문만 출력. 작업 과정 설명 금지.
        """
    ).strip() + "\n"


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
    improvement_plan_path: str = "",
    improvement_loop_state_path: str = "",
    next_improvement_tasks_path: str = "",
    followup_backlog_task_path: str = "",
    memory_selection_path: str = "",
    memory_context_path: str = "",
    operator_inputs_path: str = "",
    role_context: str = "",
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
        - {improvement_plan_path} (파일이 존재하고 비어있지 않으면 다음 라운드 전략으로 반영)
        - {improvement_loop_state_path} (strategy / scope_restriction / rollback 신호 참고)
        - {next_improvement_tasks_path} (다음 우선 작업 목록이 있으면 반드시 반영)
        - {followup_backlog_task_path} (follow-up backlog 브릿지 파일이 있으면 최우선 다음 작업으로 반영)
        - {memory_selection_path} (memory retrieval selection 결과가 있으면 참고)
        - {memory_context_path} (retrieved memory context가 있으면 참고)
        - {operator_inputs_path} (운영자가 나중에 제공할 입력/키 상태가 있으면 반영)

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
        - UI 복잡도 제어: 새 기능이 들어와도 메뉴/정보 위계를 어떻게 단순하게 유지할지
        - 벤치마크 방향: 참고할 상위권 레퍼런스 제품 1~2개와 차용할 구조 원칙

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
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        {OPERATING_ENFORCEMENT_BRIEF}
        - 각 섹션은 실행 가능한 체크리스트와 산출물 파일을 포함.
        - 계획 작성 전에 저장소의 관련 코드/문서/테스트를 직접 검색해 현재 상태를 파악.
        - 변경 파일 후보와 영향 범위를 근거 기반으로 명시.
        - UI 변경이 포함되면 모바일(360~430px) 기준 레이아웃 유지 전략을 반드시 쓴다.
        - UI 변경이 포함되면 새 패널/새 카드/새 메뉴를 추가할 때 무엇을 접거나 분리할지 함께 제시한다.
        - UI 복잡도가 이미 높은 화면은 기능 추가보다 정보 구조 단순화, 탭/메뉴 분리, 카드 축약을 우선 고려한다.
        - REVIEW.md가 있으면 TODO를 고도화 플랜에 반영.
        - IMPROVEMENT_PLAN.md / NEXT_IMPROVEMENT_TASKS.json 이 있으면 strategy와 우선순위를 계획에 직접 반영.
        - FOLLOWUP_BACKLOG_TASK.json 이 있으면 이 후보를 이번 라운드의 최우선 next action으로 반영.
        - OPERATOR_INPUTS.json 에 pending input이 있으면 차단 요소와 대체 경로를 함께 계획에 쓴다.
        - improvement strategy가 `design_rebaseline` 또는 scope_restriction이 `MVP_redefinition`이면,
          구현 확대가 아니라 제품 정의/범위/설계 문서 재정렬 계획을 우선 작성.
        - improvement strategy가 `feature_expansion`이면 품질 게이트를 깨지 않는 범위에서 사용자 가치가 높은 기능 1개만 확장.
        - improvement strategy가 `test_hardening`이면 신규 기능보다 회귀 테스트/테스트 전략 보강을 우선.
        - improvement strategy가 `ux_clarity_improvement`이면 error/empty/loading 상태와 안내 문구, 사용자 흐름 명확화부터 정리.
        - improvement strategy가 `stabilization`, `rollback_or_stabilize`, `narrow_scope_stabilization`이면 기능 확대 없이 저점 카테고리 안정화에 집중.
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
    if role_context.strip():
        base += "\n\n" + dedent(
            f"""
            Agent runtime profile:
            {role_context.strip()}
            """
        ).strip()
    memory_addendum = "\n\n".join(
        block
        for block in [
            _read_prompt_context(followup_backlog_task_path, label="Follow-up Backlog Task"),
            _read_prompt_context(memory_selection_path, label="Memory Selection"),
            _read_prompt_context(memory_context_path, label="Memory Context"),
            _read_prompt_context(operator_inputs_path, label="Operator Inputs"),
        ]
        if block
    ).strip()
    if memory_addendum:
        base += "\n\n" + memory_addendum
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
    improvement_plan_path: str = "",
    improvement_loop_state_path: str = "",
    next_improvement_tasks_path: str = "",
    memory_selection_path: str = "",
    memory_context_path: str = "",
    operator_inputs_path: str = "",
    role_context: str = "",
) -> str:
    """Prompt text for coder model (Codex)."""

    # We include both PLAN and REVIEW references in one prompt shape so the same
    # command template can be reused for implementation and fix stages.
    base = dedent(
        f"""
        Goal: {coding_goal}

        {plan_path}의 지시를 따르세요.
        특히 PLAN.md의 "Design intent and style direction"을 구현에 반드시 반영하세요.
        {design_path}가 존재하고 비어있지 않으면 DESIGN_SYSTEM.md의 토큰/컴포넌트/반응형 규칙을 구현에 반영하세요.
        {design_tokens_path}가 존재하면 토큰(theme/color/typography/spacing)을 코드 변수에 매핑하세요.
        {token_handoff_path}가 존재하면 인계 체크리스트를 우선 반영하세요.
        {publish_handoff_path}가 존재하면 퍼블리셔 인계사항을 우선 반영하세요.
        {review_path}가 존재하고 비어있지 않으면 TODO 항목을 반영하세요.
        {improvement_plan_path}가 존재하고 비어있지 않으면 개선 전략과 scope restriction을 우선 반영하세요.
        {improvement_loop_state_path}가 존재하면 strategy / rollback / principle enforcement 신호를 참고하세요.
        {next_improvement_tasks_path}가 존재하고 비어있지 않으면 listed task를 우선순위대로 처리하세요.
        {operator_inputs_path}가 존재하면 운영자 제공 입력 상태를 참고하세요. secret 값은 직접 출력하지 말고 env var 존재 여부만 활용하세요.

        Requirements:
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        {OPERATING_ENFORCEMENT_BRIEF}
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
        - NEXT_IMPROVEMENT_TASKS.json의 `scope_restriction`이 `P1_only`이면 P0/P1 작업만 수행.
        - improvement strategy가 `feature_expansion`이면 사용자 가치가 높은 기능 1개만 제한적으로 추가하고 테스트를 함께 보강.
        - improvement strategy가 `test_hardening`이면 신규 기능보다 테스트/회귀 방지 보강을 우선.
        - improvement strategy가 `ux_clarity_improvement`이면 error/empty/loading 상태와 사용자 안내 문구 개선을 우선.
        - improvement strategy가 `stabilization`, `rollback_or_stabilize`, `narrow_scope_stabilization`이면 신규 기능 추가보다 기존 품질 안정화 우선.
        - improvement strategy가 `quality_hardening`이면 레거시 전략으로 간주하고 안정성/테스트/에러/빈/로딩 상태 보강을 우선.
        - 회귀(regression) 유발 금지.
        - 보안 민감정보 하드코딩 금지.
        - secret input은 코드/로그/문서에 그대로 출력하지 말고 env var 참조로만 연결.
        - 실패 시 우회가 아닌 원인 기반 수정 우선.
        - UI 변경 시 모바일(360~430px) 우선으로 레이아웃이 깨지지 않게 유지.
        - 화면 복잡도가 이미 높으면 새 UI를 덧대기보다 탭/메뉴/접힘 구조로 분리.
        - 스타일은 모던하고 심플하게 유지하되, 기능이 늘수록 정보 밀도를 낮추는 방향으로 정리.

        개발 체크리스트:
        1. 요구사항 충족: SPEC/PLAN/MVP_SCOPE 범위를 벗어나지 않았는가?
        2. UI 일관성: 라이트/다크, 반응형, 접근성이 유지되는가?
        3. 안정성: 예외/에러/빈 상태/로딩 상태 처리가 포함되는가?
        4. 검증성: 변경 사항을 확인할 테스트/실행 방법이 있는가?
        5. 유지보수성: 최소 변경으로 명확한 구조를 유지했는가?
        6. 반복 방지: 같은 문제를 기계적으로 다시 수정하는 패턴이 아닌가?
        """
    ).strip()
    if role_context.strip():
        base += "\n\n" + dedent(
            f"""
            Agent runtime profile:
            {role_context.strip()}
            """
        ).strip()
    memory_addendum = "\n\n".join(
        block
        for block in [
            _read_prompt_context(memory_selection_path, label="Memory Selection"),
            _read_prompt_context(memory_context_path, label="Memory Context"),
            _read_prompt_context(operator_inputs_path, label="Operator Inputs"),
        ]
        if block
    ).strip()
    if memory_addendum:
        base += "\n\n" + memory_addendum
    return base + "\n\n" + SEARCH_TOOL_GUIDE + "\n"



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


def build_copywriter_prompt(
    *,
    spec_path: str,
    plan_path: str,
    design_path: str,
    publish_handoff_path: str,
    copy_plan_path: str,
    copy_deck_path: str,
) -> str:
    """Prompt text for copywriter step (Korean-first UX copy artifacts)."""

    return dedent(
        f"""
        Goal: 카피라이터 단계 수행(고객 친화 한글 카피 설계 + 실제 문구 작성)

        참고 자료:
        - Spec: {spec_path}
        - Plan: {plan_path}
        - Design system: {design_path}
        - Publish handoff: {publish_handoff_path}

        필수 산출물:
        1. {copy_plan_path}
           - 기획 의도 요약(사용자/상황/행동 목표)
           - 톤앤매너(쉽고 친근한 한국어 중심)
           - 화면/기능별 카피 전략(헤드라인/보조문구/버튼/에러/빈상태)
           - 금지 표현/주의 표현
        2. {copy_deck_path}
           - 실제 적용 가능한 한국어 카피 문구 모음
           - 섹션별로 `원문(기존) -> 제안(개선)` 형식 포함
           - CTA 버튼 문구는 짧고 명확하게(2~8자 권장)
           - 에러/안내 문구는 해결 행동을 함께 제시

        품질 기준:
        - 초등학생도 이해 가능한 쉬운 단어 우선
        - 과장/모호/중복 표현 최소화
        - 기능 의도와 문구가 불일치하면 문구를 기능 의도에 맞춰 조정

        출력 규칙:
        - 반드시 한국어 중심(고유명사/파일명은 영문 가능)
        - 작업 과정 설명 금지
        - 산출물 본문만 작성
        """
    ).strip() + "\n\n" + SEARCH_TOOL_GUIDE + "\n"


def build_documentation_prompt(
    *,
    spec_path: str,
    plan_path: str,
    review_path: str,
    readme_path: str,
    copyright_path: str,
    development_guide_path: str,
    documentation_plan_path: str,
) -> str:
    """Prompt text for documentation stage (Claude-first, Codex fallback)."""

    return dedent(
        f"""
        Goal: PR 전에 프로젝트 필수 개발 문서를 최신 상태로 작성/갱신한다.

        참고 자료:
        - Spec: {spec_path}
        - Plan: {plan_path}
        - Review: {review_path}

        필수 산출물:
        - {readme_path}
        - {copyright_path}
        - {development_guide_path}
        - {documentation_plan_path}

        내용 기준:
        1) README.md
           - 프로젝트 목적/핵심 기능
           - 빠른 시작(설치, 실행, 테스트)
           - 환경변수/디렉토리 구조 요약
        2) COPYRIGHT.md
           - 저작권 고지 템플릿
           - 제3자 라이선스 확인 가이드(placeholder 허용)
        3) DEVELOPMENT_GUIDE.md
           - 개발 워크플로우(브랜치, 테스트, PR)
           - 에이전트/오케스트레이션 사용 가이드
           - 장애 대응 기본 체크리스트
        4) DOCUMENTATION_PLAN.md
           - 이번 라운드 문서 변경 요약
           - 유지보수 시 갱신해야 할 섹션 목록

        출력 형식(엄수):
        - 아래 마커 포맷으로만 출력하고, 다른 설명 문장은 쓰지 않는다.
        - 각 FILE 블록은 반드시 1개 이상 본문 줄을 포함한다.
        <<<FILE:README.md>>>
        ...본문...
        <<<FILE:COPYRIGHT.md>>>
        ...본문...
        <<<FILE:DEVELOPMENT_GUIDE.md>>>
        ...본문...
        <<<FILE:_docs/DOCUMENTATION_PLAN.md>>>
        ...본문...
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


def build_reviewer_prompt(
    spec_path: str,
    plan_path: str,
    review_path: str,
    memory_selection_path: str = "",
    memory_context_path: str = "",
    role_context: str = "",
) -> str:
    """Prompt text for reviewer model (Gemini)."""

    base = dedent(
        f"""
        당신은 REVIEW.md의 최종 markdown 본문을 생성합니다.

        아래 자료를 기반으로 현재 저장소 상태를 리뷰하세요:
        - Spec: {spec_path}
        - Plan: {plan_path}

        출력 대상 경로(참고용):
        - {review_path}

        필수 리뷰 카테고리:
        - Code quality
        - Architecture structure
        - Maintainability
        - Usability
        - UX clarity
        - Test coverage
        - Error state handling
        - Empty state handling
        - Loading state handling
        - Functional bugs
        - Security concerns
        - Missing tests / weak test coverage
        - Edge cases

        마지막에는 coder를 위한 TODO checklist를 추가하세요.

        작성 규칙:
        - 반드시 한국어로 작성.
        - 문서명과 카테고리 명칭(예: REVIEW, TODO, Functional bugs)은 영문 유지.
        - 상세 설명은 한국어로 작성.
        - 아래 운영 원칙을 따른다.
        {OPERATING_PRINCIPLES_BRIEF}
        {OPERATING_ENFORCEMENT_BRIEF}
        - 실행/재현 예시에서 포트가 나오면 3100번대만 사용.
        - 같은 문제의 반복 여부와 품질 개선 정체 여부를 반드시 지적한다.
        - "코드가 돌아간다"는 이유만으로 합격 처리하지 말고 제품 품질 기준으로 판단한다.
        - markdown 본문만 출력하고 작업 과정/내부 추론/메타 코멘트 금지.
        - 출력 내 후속 질문 금지.
        """
    ).strip()
    if role_context.strip():
        base += "\n\n" + dedent(
            f"""
            Agent runtime profile:
            {role_context.strip()}
            """
        ).strip()
    memory_addendum = "\n\n".join(
        block
        for block in [
            _read_prompt_context(memory_selection_path, label="Memory Selection"),
            _read_prompt_context(memory_context_path, label="Memory Context"),
        ]
        if block
    ).strip()
    if memory_addendum:
        base += "\n\n" + memory_addendum
    return base + "\n"



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
