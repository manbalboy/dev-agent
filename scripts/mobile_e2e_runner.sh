#!/usr/bin/env bash
set -euo pipefail

PLATFORM=""
REPO_DIR="${PWD}"
DOCS_DIR="$REPO_DIR/_docs"
RESULT_PATH="$DOCS_DIR/MOBILE_E2E_RESULT.json"
STATUS="blocked"
EXIT_CODE=1
RUNNER="unknown"
COMMAND=""
TARGET_NAME=""
TARGET_ID=""
BOOTED="false"
NOTES=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/mobile_e2e_runner.sh --platform android
  bash scripts/mobile_e2e_runner.sh --platform ios

Environment:
  AGENTHUB_ANDROID_AVD_NAME
  AGENTHUB_IOS_SIMULATOR_NAME
  AGENTHUB_MOBILE_E2E_COMMAND_ANDROID
  AGENTHUB_MOBILE_E2E_COMMAND_IOS
  AGENTHUB_DETOX_ANDROID_CONFIG
  AGENTHUB_DETOX_IOS_CONFIG
EOF
}

log() {
  printf '[mobile-e2e] %s\n' "$1"
}

ensure_docs_dir() {
  mkdir -p "$DOCS_DIR"
}

write_result() {
  ensure_docs_dir
  python3 - "$RESULT_PATH" "$PLATFORM" "$TARGET_NAME" "$TARGET_ID" "$BOOTED" "$COMMAND" "$EXIT_CODE" "$STATUS" "$RUNNER" "$NOTES" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "platform": sys.argv[2],
    "target_name": sys.argv[3],
    "target_id": sys.argv[4],
    "booted": sys.argv[5].lower() == "true",
    "command": sys.argv[6],
    "exit_code": int(sys.argv[7]),
    "status": sys.argv[8],
    "runner": sys.argv[9],
    "notes": sys.argv[10],
}
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

fail() {
  NOTES="$1"
  STATUS="blocked"
  EXIT_CODE="${2:-1}"
  write_result
  log "$NOTES"
  exit "$EXIT_CODE"
}

has_npm_script() {
  local name="$1"
  [ -f package.json ] || return 1
  rg -n "\"${name}\"\\s*:" package.json >/dev/null 2>&1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --platform)
        PLATFORM="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "unknown argument: $1" 2
        ;;
    esac
  done

  case "$PLATFORM" in
    android|ios) ;;
    *)
      fail "platform must be one of: android, ios" 2
      ;;
  esac
}

choose_first_line() {
  awk 'NF { print; exit }'
}

parse_first_booted_android_device() {
  awk '/^emulator-[0-9]+[[:space:]]+device$/ { print $1; exit }'
}

parse_ios_device() {
  local mode="$1"
  local preferred_name="${2:-}"
  python3 -c '
import json
import sys

mode = sys.argv[1]
preferred_name = sys.argv[2].strip().lower()
payload = json.load(sys.stdin)
devices = payload.get("devices", {}) if isinstance(payload, dict) else {}
candidates = []
for _, items in devices.items():
    if not isinstance(items, list):
        continue
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        udid = str(item.get("udid", "")).strip()
        is_available = bool(item.get("isAvailable", True))
        state = str(item.get("state", "")).strip().lower()
        if not name or not udid:
            continue
        if mode == "booted":
          if state == "booted":
            candidates.append((name, udid))
        else:
          if is_available:
            candidates.append((name, udid))

if preferred_name:
    for name, udid in candidates:
        if name.lower() == preferred_name:
            print(f"{name}|{udid}")
            raise SystemExit(0)

for name, udid in candidates:
    if "iphone" in name.lower():
        print(f"{name}|{udid}")
        raise SystemExit(0)

if candidates:
    name, udid = candidates[0]
    print(f"{name}|{udid}")
    raise SystemExit(0)

raise SystemExit(1)
' "$mode" "$preferred_name"
}

resolve_android_target() {
  if ! command -v adb >/dev/null 2>&1; then
    fail "adb not found. Android emulator E2E requires Android SDK platform-tools." 1
  fi
  if ! command -v emulator >/dev/null 2>&1; then
    fail "emulator not found. Android emulator E2E requires Android SDK emulator." 1
  fi

  local booted_id=""
  booted_id="$(adb devices | parse_first_booted_android_device || true)"
  if [[ -n "$booted_id" ]]; then
    TARGET_ID="$booted_id"
    TARGET_NAME="${AGENTHUB_ANDROID_AVD_NAME:-$booted_id}"
    BOOTED="true"
    NOTES="reused already booted android emulator"
    return 0
  fi

  TARGET_NAME="${AGENTHUB_ANDROID_AVD_NAME:-}"
  if [[ -z "$TARGET_NAME" ]]; then
    TARGET_NAME="$(emulator -list-avds | choose_first_line || true)"
  fi
  if [[ -z "$TARGET_NAME" ]]; then
    fail "no Android AVD found. Set AGENTHUB_ANDROID_AVD_NAME or create an emulator." 1
  fi

  log "starting android emulator: ${TARGET_NAME}"
  nohup emulator -avd "$TARGET_NAME" >/dev/null 2>&1 &
  adb wait-for-device >/dev/null 2>&1 || true

  local boot_completed=""
  local attempt=0
  for attempt in $(seq 1 120); do
    boot_completed="$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' | tr -d '\n' || true)"
    if [[ "$boot_completed" == "1" ]]; then
      break
    fi
    sleep 2
  done
  if [[ "$boot_completed" != "1" ]]; then
    fail "android emulator boot did not complete in time for ${TARGET_NAME}" 1
  fi

  TARGET_ID="$(adb devices | parse_first_booted_android_device || true)"
  TARGET_ID="${TARGET_ID:-emulator}"
  BOOTED="true"
  NOTES="booted android emulator for E2E"
}

resolve_ios_target() {
  if ! command -v xcrun >/dev/null 2>&1; then
    fail "xcrun not found. iOS simulator E2E requires Xcode command line tools." 1
  fi

  local booted_pair=""
  booted_pair="$(xcrun simctl list devices booted -j | parse_ios_device booted "${AGENTHUB_IOS_SIMULATOR_NAME:-}" || true)"
  if [[ -n "$booted_pair" ]]; then
    TARGET_NAME="${booted_pair%%|*}"
    TARGET_ID="${booted_pair#*|}"
    BOOTED="true"
    NOTES="reused already booted ios simulator"
    return 0
  fi

  local available_pair=""
  available_pair="$(xcrun simctl list devices available -j | parse_ios_device available "${AGENTHUB_IOS_SIMULATOR_NAME:-}" || true)"
  if [[ -z "$available_pair" ]]; then
    fail "no available iOS simulator found. Set AGENTHUB_IOS_SIMULATOR_NAME or create a simulator." 1
  fi
  TARGET_NAME="${available_pair%%|*}"
  TARGET_ID="${available_pair#*|}"

  log "booting ios simulator: ${TARGET_NAME} (${TARGET_ID})"
  xcrun simctl boot "$TARGET_ID" >/dev/null 2>&1 || true
  if command -v open >/dev/null 2>&1; then
    open -a Simulator --args -CurrentDeviceUDID "$TARGET_ID" >/dev/null 2>&1 || true
  fi
  xcrun simctl bootstatus "$TARGET_ID" -b >/dev/null 2>&1

  BOOTED="true"
  NOTES="booted ios simulator for E2E"
}

resolve_command() {
  if [[ "$PLATFORM" == "android" ]]; then
    if [[ -n "${AGENTHUB_MOBILE_E2E_COMMAND_ANDROID:-}" ]]; then
      RUNNER="custom_command"
      COMMAND="${AGENTHUB_MOBILE_E2E_COMMAND_ANDROID}"
      return 0
    fi
    if has_npm_script "test:e2e:android"; then
      RUNNER="npm_script"
      COMMAND="npm run test:e2e:android"
      return 0
    fi
    if has_npm_script "e2e:android"; then
      RUNNER="npm_script"
      COMMAND="npm run e2e:android"
      return 0
    fi
    if has_npm_script "detox:android"; then
      RUNNER="npm_script"
      COMMAND="npm run detox:android"
      return 0
    fi
    if [[ -n "${AGENTHUB_DETOX_ANDROID_CONFIG:-}" ]]; then
      RUNNER="detox"
      COMMAND="npx detox test --configuration ${AGENTHUB_DETOX_ANDROID_CONFIG}"
      return 0
    fi
  else
    if [[ -n "${AGENTHUB_MOBILE_E2E_COMMAND_IOS:-}" ]]; then
      RUNNER="custom_command"
      COMMAND="${AGENTHUB_MOBILE_E2E_COMMAND_IOS}"
      return 0
    fi
    if has_npm_script "test:e2e:ios"; then
      RUNNER="npm_script"
      COMMAND="npm run test:e2e:ios"
      return 0
    fi
    if has_npm_script "e2e:ios"; then
      RUNNER="npm_script"
      COMMAND="npm run e2e:ios"
      return 0
    fi
    if has_npm_script "detox:ios"; then
      RUNNER="npm_script"
      COMMAND="npm run detox:ios"
      return 0
    fi
    if [[ -n "${AGENTHUB_DETOX_IOS_CONFIG:-}" ]]; then
      RUNNER="detox"
      COMMAND="npx detox test --configuration ${AGENTHUB_DETOX_IOS_CONFIG}"
      return 0
    fi
  fi

  fail "no mobile E2E command found for platform=${PLATFORM}. Add npm scripts or AGENTHUB_MOBILE_E2E_COMMAND_${PLATFORM^^}." 1
}

main() {
  parse_args "$@"

  case "$PLATFORM" in
    android) resolve_android_target ;;
    ios) resolve_ios_target ;;
  esac
  resolve_command

  log "running ${PLATFORM} mobile E2E with ${RUNNER}"
  set +e
  bash -lc "$COMMAND"
  EXIT_CODE=$?
  set -e

  if [[ "$EXIT_CODE" -eq 0 ]]; then
    STATUS="passed"
  else
    STATUS="failed"
  fi
  write_result
  exit "$EXIT_CODE"
}

main "$@"
