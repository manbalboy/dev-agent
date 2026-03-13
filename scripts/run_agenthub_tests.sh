#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-auto}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOBILE_E2E_RUNNER="${AGENTHUB_MOBILE_E2E_RUNNER:-$SCRIPT_DIR/mobile_e2e_runner.sh}"

log() {
  printf '[agenthub-test] %s\n' "$1"
}

build_pythonpath() {
  if [ -n "${PYTHONPATH:-}" ]; then
    printf '.:%s' "$PYTHONPATH"
    return 0
  fi
  printf '.'
}

has_npm_script() {
  local name="$1"
  [ -f package.json ] || return 1
  rg -n "\"${name}\"\\s*:" package.json >/dev/null 2>&1
}

has_mobile_e2e_command() {
  local platform="$1"
  case "$platform" in
    android)
      [ -n "${AGENTHUB_MOBILE_E2E_COMMAND_ANDROID:-}" ] && return 0
      [ -n "${AGENTHUB_DETOX_ANDROID_CONFIG:-}" ] && return 0
      has_npm_script "test:e2e:android" && return 0
      has_npm_script "e2e:android" && return 0
      has_npm_script "detox:android" && return 0
      ;;
    ios)
      [ -n "${AGENTHUB_MOBILE_E2E_COMMAND_IOS:-}" ] && return 0
      [ -n "${AGENTHUB_DETOX_IOS_CONFIG:-}" ] && return 0
      has_npm_script "test:e2e:ios" && return 0
      has_npm_script "e2e:ios" && return 0
      has_npm_script "detox:ios" && return 0
      ;;
  esac
  return 1
}

resolve_mobile_e2e_platform() {
  local preferred="${AGENTHUB_MOBILE_E2E_DEFAULT_PLATFORM:-}"
  if [ -n "$preferred" ] && has_mobile_e2e_command "$preferred"; then
    printf '%s' "$preferred"
    return 0
  fi
  if has_mobile_e2e_command "android"; then
    printf 'android'
    return 0
  fi
  if has_mobile_e2e_command "ios"; then
    printf 'ios'
    return 0
  fi
  return 0
}

run_mobile_e2e() {
  local platform="$1"
  if [ ! -f "$MOBILE_E2E_RUNNER" ]; then
    log "mobile e2e runner not found: $MOBILE_E2E_RUNNER"
    return 2
  fi
  log "running mobile e2e (${platform})"
  bash "$MOBILE_E2E_RUNNER" --platform "$platform"
}

run_npm_script() {
  local name="$1"
  log "running npm script: ${name}"
  npm run "$name"
}

run_npm_test() {
  log "running npm test"
  npm test -- --run
}

run_playwright() {
  log "running playwright e2e"
  npx playwright test
}

has_python_test_layout() {
  find tests -type f \( -name 'test_*.py' -o -name '*_test.py' \) -print -quit 2>/dev/null | grep -q .
}

has_python_project() {
  [ -f pytest.ini ] && return 0
  [ -f setup.py ] && return 0
  [ -f setup.cfg ] && return 0
  [ -f requirements.txt ] && return 0
  [ -f requirements-dev.txt ] && return 0
  [ -f Pipfile ] && return 0
  [ -f manage.py ] && return 0

  if [ -f pyproject.toml ] && rg -n 'pytest|python|tool\.pytest|project' pyproject.toml >/dev/null 2>&1; then
    return 0
  fi

  if [ -d tests ] && has_python_test_layout; then
    [ -d app ] && return 0
    [ -d src ] && return 0
    find . -path './tests' -prune -o -type f -name '*.py' -print -quit 2>/dev/null | grep -q . && return 0
  fi

  return 1
}

run_pytest() {
  local pythonpath
  local python_bin=""
  pythonpath="$(build_pythonpath)"
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ] && "${VIRTUAL_ENV}/bin/python" -c 'import pytest' >/dev/null 2>&1; then
    python_bin="${VIRTUAL_ENV}/bin/python"
  elif [ -x .venv/bin/python ] && .venv/bin/python -c 'import pytest' >/dev/null 2>&1; then
    python_bin=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1 && python3 -c 'import pytest' >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  elif command -v python >/dev/null 2>&1 && python -c 'import pytest' >/dev/null 2>&1; then
    python_bin="$(command -v python)"
  fi

  if [ -n "$python_bin" ]; then
    log "running pytest with ${python_bin} -m pytest (PYTHONPATH=${pythonpath})"
    env PYTHONPATH="$pythonpath" "$python_bin" -m pytest -q
    return 0
  fi
  log "running pytest entrypoint with PYTHONPATH=${pythonpath}"
  env PYTHONPATH="$pythonpath" pytest -q
}

skip_tests() {
  log "no executable e2e/test command found in $(pwd); skipping"
  return 0
}

run_e2e_first() {
  local mobile_platform=""
  mobile_platform="$(resolve_mobile_e2e_platform)"
  if [ -n "$mobile_platform" ]; then
    run_mobile_e2e "$mobile_platform"
    return 0
  fi
  if command -v npm >/dev/null 2>&1 && has_npm_script "test:e2e"; then
    run_npm_script "test:e2e"
    return 0
  fi
  if command -v npx >/dev/null 2>&1 && { [ -f playwright.config.js ] || [ -f playwright.config.ts ] || [ -d test/e2e ] || [ -d tests/e2e ]; }; then
    run_playwright
    return 0
  fi
  if command -v npm >/dev/null 2>&1 && has_npm_script "test"; then
    run_npm_test
    return 0
  fi
  if has_python_project; then
    run_pytest
    return 0
  fi
  skip_tests
}

run_implement_first() {
  if command -v npm >/dev/null 2>&1 && has_npm_script "test"; then
    run_npm_test
    return 0
  fi
  if has_python_project; then
    run_pytest
    return 0
  fi
  run_e2e_first
}

case "$MODE" in
  mobile-e2e-android)
    run_mobile_e2e "android"
    ;;
  mobile-e2e-ios)
    run_mobile_e2e "ios"
    ;;
  e2e|fix)
    run_e2e_first
    ;;
  implement)
    run_implement_first
    ;;
  auto)
    run_implement_first
    ;;
  *)
    log "unknown mode: ${MODE}"
    exit 2
    ;;
esac
