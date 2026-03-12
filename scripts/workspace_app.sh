#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${AGENTHUB_ROOT_DIR:-/home/docker/agentHub}"
ACTION="start"
APP_CODE=""
REPO_SLUG=""
WORKSPACE_DIR=""
MAP_FILE=""
PID_DIR=""
LOG_DIR=""
FORCE_INSTALL="false"
RUN_MODE="web"
META_DIR=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/workspace_app.sh start  --app <app_code> [--repo owner/repo] [--install] [--mode web|expo-android|expo-ios|rn-android|rn-ios]
  bash scripts/workspace_app.sh stop   --app <app_code>
  bash scripts/workspace_app.sh status --app <app_code>

Behavior:
  - `web` mode auto-assigns and persists app port in 3100-3199 range.
  - mobile mode runs explicit Expo/React Native emulator commands and does not allocate app ports.
  - Port mapping file: config/app_ports.json
  - PID file: data/pids/app_<app_code>.pid
  - Meta file: data/pids/app_<app_code>.json
  - Log file: data/logs/apps/<app_code>.log
EOF
}

sanitize_app() {
  local raw="$1"
  echo "$raw" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-'
}

repo_to_safe() {
  local repo="$1"
  echo "${repo//\//__}"
}

ensure_map_file() {
  mkdir -p "$(dirname "$MAP_FILE")"
  if [[ ! -f "$MAP_FILE" ]]; then
    echo "{}" > "$MAP_FILE"
  fi
}

get_or_assign_port() {
  local app="$1"
  python3 - "$MAP_FILE" "$app" <<'PY'
import json
import socket
import sys
from pathlib import Path

map_path = Path(sys.argv[1])
app = sys.argv[2]
if not map_path.exists():
    map_path.write_text("{}\n", encoding="utf-8")

try:
    data = json.loads(map_path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    data = {}
if not isinstance(data, dict):
    data = {}

existing = data.get(app)
if isinstance(existing, int) and 3100 <= existing <= 3199:
    print(existing)
    sys.exit(0)

assigned = {int(v) for v in data.values() if isinstance(v, int)}

def in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex(("127.0.0.1", port)) == 0

selected = None
for port in range(3100, 3200):
    if port in assigned:
        continue
    if in_use(port):
        continue
    selected = port
    break

if selected is None:
    print("NO_PORT", file=sys.stderr)
    sys.exit(2)

data[app] = selected
map_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(selected)
PY
}

read_port() {
  local app="$1"
  python3 - "$MAP_FILE" "$app" <<'PY'
import json
import sys
from pathlib import Path

map_path = Path(sys.argv[1])
app = sys.argv[2]
if not map_path.exists():
    sys.exit(1)
try:
    data = json.loads(map_path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    sys.exit(1)
port = data.get(app)
if isinstance(port, int):
    print(port)
    sys.exit(0)
sys.exit(1)
PY
}

parse_args() {
  if [[ $# -ge 1 ]]; then
    ACTION="$1"
    shift
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --app)
        APP_CODE="${2:-}"
        shift 2
        ;;
      --repo)
        REPO_SLUG="${2:-}"
        shift 2
        ;;
      --install)
        FORCE_INSTALL="true"
        shift
        ;;
      --mode)
        RUN_MODE="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown argument: $1"
        usage
        exit 1
        ;;
    esac
  done
}

load_env() {
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.env"
    set +a
  fi

  WORKSPACE_DIR="${AGENTHUB_WORKSPACE_DIR:-$ROOT_DIR/workspaces}"
  MAP_FILE="$ROOT_DIR/config/app_ports.json"
  PID_DIR="$ROOT_DIR/data/pids"
  LOG_DIR="$ROOT_DIR/data/logs/apps"
  META_DIR="$ROOT_DIR/data/pids"

  if [[ -z "$REPO_SLUG" ]]; then
    REPO_SLUG="${AGENTHUB_ALLOWED_REPOSITORY:-manbalboy/agent-hub}"
  fi
}

mode_requires_port() {
  local mode="$1"
  [[ "$mode" == "web" ]]
}

resolve_run_command() {
  local mode="$1"
  case "$mode" in
    web)
      echo "exec npm start"
      ;;
    expo-android)
      echo "exec npx expo start --android"
      ;;
    expo-ios)
      echo "exec npx expo start --ios"
      ;;
    rn-android)
      echo "exec npm run android"
      ;;
    rn-ios)
      echo "exec npm run ios"
      ;;
    *)
      echo ""
      ;;
  esac
}

write_meta_file() {
  local meta_file="$1"
  local app="$2"
  local repo_slug="$3"
  local mode="$4"
  local command="$5"
  local log_file="$6"
  local pid="$7"
  local port="$8"

  python3 - "$meta_file" "$app" "$repo_slug" "$mode" "$command" "$log_file" "$pid" "$port" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

meta_path = Path(sys.argv[1])
payload = {
    "app_code": sys.argv[2],
    "repository": sys.argv[3],
    "mode": sys.argv[4],
    "command": sys.argv[5],
    "log_file": sys.argv[6],
    "pid": sys.argv[7],
    "port": sys.argv[8],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

read_meta_value() {
  local meta_file="$1"
  local key="$2"
  python3 - "$meta_file" "$key" <<'PY'
import json
import sys
from pathlib import Path

meta_path = Path(sys.argv[1])
key = sys.argv[2]
if not meta_path.exists():
    sys.exit(1)
try:
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    sys.exit(1)
value = payload.get(key)
if value in (None, ""):
    sys.exit(1)
print(value)
PY
}

start_app() {
  local app="$1"
  local repo_safe="$2"
  local repo_path="$WORKSPACE_DIR/$app/$repo_safe"
  local pid_file="$PID_DIR/app_${app}.pid"
  local meta_file="$META_DIR/app_${app}.json"
  local log_file="$LOG_DIR/${app}.log"

  if [[ ! -d "$repo_path" ]]; then
    echo "Workspace not found: $repo_path"
    exit 1
  fi

  mkdir -p "$PID_DIR" "$LOG_DIR" "$META_DIR"
  local port=""
  if mode_requires_port "$RUN_MODE"; then
    ensure_map_file
    port="$(get_or_assign_port "$app")"
  fi

  if [[ -f "$pid_file" ]]; then
    local old_pid
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      local running_mode="$RUN_MODE"
      if read_meta_value "$meta_file" "mode" >/tmp/agenthub_app_mode.$$ 2>/dev/null; then
        running_mode="$(cat /tmp/agenthub_app_mode.$$)"
        rm -f /tmp/agenthub_app_mode.$$
      fi
      echo "Already running: app=$app pid=$old_pid mode=$running_mode${port:+ port=$port}"
      if [[ -n "$port" ]]; then
        echo "URL: http://$(hostname -I | awk '{print $1}'):$port"
      fi
      exit 0
    fi
  fi

  if [[ "$FORCE_INSTALL" == "true" ]] || [[ ! -d "$repo_path/node_modules" ]]; then
    echo "[INFO] Installing npm dependencies..."
    (cd "$repo_path" && npm install --no-audit --no-fund)
  fi

  local run_command
  run_command="$(resolve_run_command "$RUN_MODE")"
  if [[ -z "$run_command" ]]; then
    echo "Unsupported mode: $RUN_MODE"
    usage
    exit 1
  fi

  echo "[INFO] Starting app=$app mode=$RUN_MODE${port:+ port=$port}"
  (
    cd "$repo_path"
    if [[ -n "$port" ]]; then
      nohup env PORT="$port" bash -lc "$run_command" > "$log_file" 2>&1 &
    else
      nohup bash -lc "$run_command" > "$log_file" 2>&1 &
    fi
    echo $! > "$pid_file"
  )

  local new_pid
  new_pid="$(cat "$pid_file")"
  write_meta_file "$meta_file" "$app" "$REPO_SLUG" "$RUN_MODE" "$run_command" "$log_file" "$new_pid" "$port"
  sleep 1
  if ! kill -0 "$new_pid" 2>/dev/null; then
    echo "Failed to start app. Check log: $log_file"
    exit 1
  fi

  echo "Started: app=$app pid=$new_pid mode=$RUN_MODE${port:+ port=$port}"
  if [[ -n "$port" ]]; then
    echo "URL: http://$(hostname -I | awk '{print $1}'):$port"
  fi
  echo "Command: $run_command"
  echo "Log: $log_file"
}

stop_app() {
  local app="$1"
  local pid_file="$PID_DIR/app_${app}.pid"
  local meta_file="$META_DIR/app_${app}.json"

  if [[ ! -f "$pid_file" ]]; then
    echo "No PID file for app=$app"
    rm -f "$meta_file"
    exit 0
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" || true
    fi
    echo "Stopped: app=$app pid=$pid"
  else
    echo "Process not running for app=$app"
  fi
  rm -f "$pid_file"
  rm -f "$meta_file"
}

status_app() {
  local app="$1"
  local pid_file="$PID_DIR/app_${app}.pid"
  local meta_file="$META_DIR/app_${app}.json"
  local port="(unassigned)"
  local mode="web"
  local command="(unknown)"
  if read_meta_value "$meta_file" "mode" >/tmp/agenthub_app_mode.$$ 2>/dev/null; then
    mode="$(cat /tmp/agenthub_app_mode.$$)"
    rm -f /tmp/agenthub_app_mode.$$
  fi
  if read_meta_value "$meta_file" "command" >/tmp/agenthub_app_cmd.$$ 2>/dev/null; then
    command="$(cat /tmp/agenthub_app_cmd.$$)"
    rm -f /tmp/agenthub_app_cmd.$$
  fi
  if read_port "$app" >/tmp/agenthub_port.$$ 2>/dev/null; then
    port="$(cat /tmp/agenthub_port.$$)"
    rm -f /tmp/agenthub_port.$$
  fi

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "RUNNING app=$app pid=$pid mode=$mode port=$port"
      echo "COMMAND $command"
      exit 0
    fi
  fi
  echo "STOPPED app=$app mode=$mode port=$port"
  echo "COMMAND $command"
}

main() {
  parse_args "$@"
  load_env

  APP_CODE="$(sanitize_app "$APP_CODE")"
  if [[ -z "$APP_CODE" ]]; then
    echo "--app is required."
    usage
    exit 1
  fi

  local repo_safe
  repo_safe="$(repo_to_safe "$REPO_SLUG")"

  case "$ACTION" in
    start)
      start_app "$APP_CODE" "$repo_safe"
      ;;
    stop)
      stop_app "$APP_CODE"
      ;;
    status)
      status_app "$APP_CODE"
      ;;
    *)
      echo "Unsupported action: $ACTION"
      usage
      exit 1
      ;;
  esac
}

main "$@"
