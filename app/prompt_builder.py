"""Prompt and markdown content builders for orchestration stages."""

from __future__ import annotations

from textwrap import dedent



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

    safe_body = issue_body.strip() or "(Issue body is empty.)"
    cors_text = preview_cors_origins.strip() or "https://manbalboy.com, http://manbalboy.com, http://localhost"
    return dedent(
        f"""
        # SPEC

        - Repository: {repository}
        - Issue: #{issue_number}
        - URL: {issue_url}
        - Title: {issue_title}

        ## 원본 요청

        {safe_body}

        ## Rule Of Engagement

        - 오케스트레이터가 단계 순서와 재시도 정책을 결정합니다.
        - AI 도구는 컨트롤러가 아니라 작업자(worker)입니다.
        - 변경 범위는 MVP에 맞게 최소화합니다.
        - 구현 단계에서 로컬 실행 포트가 필요하면 충돌 방지를 고려합니다.

        ## Deployment & Preview Requirements

        - 1회 실행 사이클의 결과물은 Docker 실행 가능 상태를 목표로 구현합니다.
        - Preview 외부 노출 포트는 {preview_port_start}-{preview_port_end} 범위를 사용합니다.
        - Preview 외부 기준 도메인/호스트: http://{preview_host}:{preview_port_start}
        - CORS 허용 대상은 manbalboy.com 계열 또는 localhost 계열로 제한합니다.
        - 허용 origin 정책(기준값): {cors_text}
        - PR 본문에는 Docker Preview 정보(컨테이너/포트/URL)를 포함합니다.
        """
    ).strip() + "\n"



def build_planner_prompt(
    spec_path: str,
    plan_path: str,
    review_path: str = "",
    is_long_term: bool = False,
    is_refinement_round: bool = False,
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

        Design intent and style direction 섹션 필수 항목:
        - 기획 의도: 이 기능이 사용자에게 전달해야 하는 핵심 경험/메시지
        - 디자인 풍: 예) 미니멀, 모던, 대시보드형, 카드형 등 구체 스타일
        - 시각 원칙: 컬러/패딩/마진/타이포의 방향성
        - 반응형 원칙: 모바일 우선 규칙

        Technology ruleset 섹션 필수 항목:
        - 플랫폼 분류: app / web / api 중 해당 항목 명시
        - app 이면 React Native 기반으로 계획
        - web 이면 React 또는 Nuxt 기반 라이브러리/프레임워크로 계획
        - api 가 필요하면 FastAPI 기반으로 계획

        작성 규칙:
        - 반드시 한국어로 작성.
        - 문서명과 고유 명칭(예: PLAN, MVP, TODO)은 영문 유지.
        - 본문 설명은 한국어로 작성.
        - 계획 작성 전에 저장소의 관련 코드/문서/테스트를 직접 검색해 현재 상태를 파악.
        - 변경 파일 후보와 영향 범위를 근거 기반으로 명시.
        - REVIEW.md가 있으면 TODO를 고도화 플랜에 반영.
        - 실행 가이드에 포트가 필요하면 3000번대 포트만 사용.
        - markdown 본문만 출력하고, 작업 과정 설명은 금지.
        - 도구/터미널/파일 조작 과정 언급 금지.
        - 코딩 에이전트가 바로 실행 가능한 실무형 계획으로 작성.
        """
    ).strip()

    if not is_long_term:
        if not is_refinement_round:
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
        return base + "\n\n" + refinement_addendum + "\n"

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
        return base + "\n\n" + long_term_addendum + "\n"
    refinement_addendum = dedent(
        """
        고도화 플랜 단계 규칙(REVIEW 반영 시에만 적용):
        - REVIEW.md TODO를 우선 반영하고, 현재 구현과 자연스럽게 연결되는 인접 기능만 추가 가능합니다.
        - 톤앤매너(디자인 의도/문체/상호작용 스타일)와 일관성을 반드시 유지하세요.
        - 동떨어진 신규 기능, 도메인 이탈 기능, 과도한 범위 확장은 금지합니다.
        - 추가 기능은 최대 1~2개로 제한하고, 각 항목에 근거(왜 필요한지)와 구현 경계를 명시하세요.
        """
    ).strip()
    return base + "\n\n" + long_term_addendum + "\n\n" + refinement_addendum + "\n"



def build_coder_prompt(
    plan_path: str,
    review_path: str,
    coding_goal: str,
    design_path: str = "",
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
        """
    ).strip() + "\n"



def build_designer_prompt(spec_path: str, plan_path: str, design_path: str) -> str:
    """Prompt text for designer model (Codex) to produce DESIGN_SYSTEM.md."""

    return dedent(
        f"""
        당신은 DESIGN_SYSTEM.md의 최종 markdown 본문을 생성합니다.

        참고 자료:
        - Spec: {spec_path}
        - Plan: {plan_path}
        - PLAN.md의 "Design intent and style direction" 섹션을 기준으로 디자인 시스템을 정의하세요.

        출력 대상 경로(참고용):
        - {design_path}

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
        - markdown 본문만 출력하고 작업 과정 설명은 금지.
        - 도구/터미널/파일 조작 과정 언급 금지.
        """
    ).strip() + "\n"


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
