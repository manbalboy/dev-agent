#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-auto}"

log() {
  printf '[agenthub-test] %s\n' "$1"
}

has_npm_script() {
  local name="$1"
  [ -f package.json ] || return 1
  rg -n "\"${name}\"\\s*:" package.json >/dev/null 2>&1
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

run_pytest() {
  log "running pytest"
  pytest -q
}

run_e2e_first() {
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
  if command -v pytest >/dev/null 2>&1 && { [ -f pytest.ini ] || [ -d tests ] || [ -f pyproject.toml ]; }; then
    run_pytest
    return 0
  fi
  log "no executable e2e/test command found in $(pwd)"
  return 1
}

run_implement_first() {
  if command -v npm >/dev/null 2>&1 && has_npm_script "test"; then
    run_npm_test
    return 0
  fi
  if command -v pytest >/dev/null 2>&1 && { [ -f pytest.ini ] || [ -d tests ] || [ -f pyproject.toml ]; }; then
    run_pytest
    return 0
  fi
  run_e2e_first
}

case "$MODE" in
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
