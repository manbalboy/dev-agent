"""Assistant prompt and provider execution helpers for dashboard routes."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
from typing import Dict, List

from fastapi import HTTPException

from app.agent_cli_runtime import resolve_cli_command_prefix, resolve_codex_command_prefix


def build_log_analysis_prompt(
    *,
    assistant: str,
    question: str,
    runtime_context: str,
    focus_context: str,
    diagnosis_context: str = "",
) -> str:
    """Create one-shot prompt for assistant-driven log diagnosis."""

    return (
        f"당신은 AgentHub 로그 분석 도우미({assistant})입니다.\n"
        "목표: 로그를 근거로 문제점을 식별하고, 즉시 실행 가능한 조치안을 제시하세요.\n\n"
        "출력 규칙:\n"
        "1) 핵심 문제점 (최대 5개)\n"
        "2) 근거 로그 (각 문제점별 1~2줄)\n"
        "3) 원인 가설 (확신도 high/med/low)\n"
        "4) 즉시 조치 (명령/파일 단위)\n"
        "5) 재발 방지 제안\n"
        "- 한국어로 간결하게 작성\n"
        "- 근거 없는 단정 금지\n\n"
        f"[사용자 질문]\n{question}\n\n"
        f"[런타임 컨텍스트]\n{runtime_context}\n\n"
        + (f"[집중 분석 대상]\n{focus_context}\n\n" if focus_context else "")
        + (f"[도구 진단 컨텍스트]\n{diagnosis_context}\n\n" if diagnosis_context else "")
    )


def build_assistant_chat_prompt(
    *,
    assistant: str,
    message: str,
    history: List[Dict[str, str]],
    runtime_context: str,
    focus_context: str,
    diagnosis_context: str = "",
) -> str:
    """Create multi-turn prompt for the assistant tab."""

    history_lines: List[str] = []
    for item in history[-12:]:
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        history_lines.append(f"{role}: {content}")

    return (
        f"당신은 AgentHub 운영 AI 도우미({assistant})입니다.\n"
        "목표: 사용자의 질문에 대해 AgentHub 실행 상태, 로그, 워크플로우, 운영 리스크를 근거로 답하세요.\n\n"
        "응답 규칙:\n"
        "- 한국어로 간결하게 작성\n"
        "- 사실(관측), 추정(가설), 조치(다음 단계)를 구분해서 답변\n"
        "- 로그/상태 근거가 부족하면 무엇이 부족한지 명확히 말할 것\n"
        "- 이전 대화 문맥을 이어받되, 최신 질문에 직접 답할 것\n"
        "- 시스템 범위를 벗어나는 일반 잡담보다 운영/개발 진단을 우선할 것\n\n"
        + (f"[대화 이력]\n{chr(10).join(history_lines)}\n\n" if history_lines else "")
        + f"[런타임 컨텍스트]\n{runtime_context}\n\n"
        + (f"[집중 분석 대상]\n{focus_context}\n\n" if focus_context else "")
        + (f"[도구 진단 컨텍스트]\n{diagnosis_context}\n\n" if diagnosis_context else "")
        + f"[최신 사용자 메시지]\n{message}\n"
    )


def run_log_analyzer(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    """Dispatch one log-analysis request to selected provider CLI."""

    if assistant == "codex":
        return run_codex_log_analysis(prompt, templates)
    if assistant == "gemini":
        return run_gemini_log_analysis(prompt, templates)
    raise HTTPException(status_code=400, detail=f"지원하지 않는 assistant: {assistant}")


def run_assistant_chat_provider(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    """Dispatch one multi-turn assistant request to selected provider CLI."""

    if assistant == "codex":
        return run_codex_chat_completion(prompt, templates)
    if assistant == "gemini":
        return run_gemini_chat_completion(prompt, templates)
    raise HTTPException(status_code=400, detail=f"지원하지 않는 assistant: {assistant}")


def run_codex_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    """Run Codex CLI for the assistant tab and return text output."""

    codex_prefix = resolve_codex_command_prefix(templates)
    output_file = tempfile.NamedTemporaryFile(
        prefix="agenthub-assistant-codex-",
        suffix=".txt",
        delete=False,
    )
    output_path = Path(output_file.name)
    output_file.close()

    try:
        process = subprocess.run(
            [
                *codex_prefix,
                "exec",
                "-C",
                str(Path.cwd()),
                "--skip-git-repo-check",
                "--color",
                "never",
                "--output-last-message",
                str(output_path),
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="Codex 대화 응답이 시간 제한(180초)을 초과했습니다.") from error
    except OSError as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Codex 실행 실패: {error}") from error

    output_text = ""
    if output_path.exists():
        try:
            output_text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            output_text = ""
    output_path.unlink(missing_ok=True)
    if process.returncode != 0:
        raw_error = (process.stderr or process.stdout or "").strip()[:1000]
        raise HTTPException(
            status_code=502,
            detail=f"Codex 대화 응답 실패(exit={process.returncode}): {raw_error or '(no output)'}",
        )
    if not output_text:
        output_text = (process.stdout or "").strip()
    return output_text or "응답이 비어 있습니다."


def run_gemini_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    """Run Gemini CLI for the assistant tab and return text output."""

    prefix = resolve_cli_command_prefix("gemini", templates, env_var="AGENTHUB_GEMINI_BIN")
    try:
        process = subprocess.run(
            [
                *prefix,
                "-p",
                prompt,
                "--approval-mode",
                "yolo",
                "--model",
                "gemini-3.1-pro-preview",
                "--output-format",
                "text",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="Gemini 대화 응답이 시간 제한(180초)을 초과했습니다.") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Gemini 실행 실패: {error}") from error
    if process.returncode != 0:
        raw_error = (process.stderr or process.stdout or "").strip()[:1000]
        raise HTTPException(
            status_code=502,
            detail=f"Gemini 대화 응답 실패(exit={process.returncode}): {raw_error or '(no output)'}",
        )
    return (process.stdout or "").strip() or "응답이 비어 있습니다."


def run_codex_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Run Codex CLI for log analysis and return text output."""

    codex_prefix = resolve_codex_command_prefix(templates)
    output_file = tempfile.NamedTemporaryFile(
        prefix="agenthub-log-analysis-codex-",
        suffix=".txt",
        delete=False,
    )
    output_path = Path(output_file.name)
    output_file.close()
    try:
        process = subprocess.run(
            [
                *codex_prefix,
                "exec",
                "-C",
                str(Path.cwd()),
                "--skip-git-repo-check",
                "--color",
                "never",
                "--output-last-message",
                str(output_path),
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="Codex 로그 분석이 시간 제한(180초)을 초과했습니다.") from error
    except OSError as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Codex 실행 실패: {error}") from error

    output_text = ""
    if output_path.exists():
        try:
            output_text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            output_text = ""
    output_path.unlink(missing_ok=True)
    if process.returncode != 0:
        raw_error = (process.stderr or process.stdout or "").strip()[:1000]
        raise HTTPException(
            status_code=502,
            detail=f"Codex 로그 분석 실패(exit={process.returncode}): {raw_error or '(no output)'}",
        )
    if not output_text:
        output_text = (process.stdout or "").strip()
    return output_text or "응답이 비어 있습니다."


def run_gemini_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Run Gemini CLI for log analysis and return text output."""

    prefix = resolve_cli_command_prefix("gemini", templates, env_var="AGENTHUB_GEMINI_BIN")
    try:
        process = subprocess.run(
            [
                *prefix,
                "-p",
                prompt,
                "--approval-mode",
                "yolo",
                "--model",
                "gemini-3.1-pro-preview",
                "--output-format",
                "text",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="Gemini 로그 분석이 시간 제한(180초)을 초과했습니다.") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Gemini 실행 실패: {error}") from error
    if process.returncode != 0:
        raw_error = (process.stderr or process.stdout or "").strip()[:1000]
        raise HTTPException(
            status_code=502,
            detail=f"Gemini 로그 분석 실패(exit={process.returncode}): {raw_error or '(no output)'}",
        )
    return (process.stdout or "").strip() or "응답이 비어 있습니다."


def run_claude_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Claude alias maintained for compatibility and routed to Codex."""

    return run_codex_log_analysis(prompt, templates)


def run_copilot_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Copilot alias maintained for compatibility and routed to Codex."""

    return run_codex_log_analysis(prompt, templates)
