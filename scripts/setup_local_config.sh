#!/usr/bin/env bash
set -euo pipefail

# This script prepares runtime config files for AgentHub on a server.
# Usage:
#   bash scripts/setup_local_config.sh
#   bash scripts/setup_local_config.sh --repo owner/repo --secret your_webhook_secret

TARGET_ROOT="/home/docker/agentHub"
REPO="manbalboy/agent-hub"
SECRET=""
DEFAULT_BRANCH="main"
TEST_COMMAND="echo skip tests"
ENABLE_ESCALATION="false"
API_PORT="8321"
DANGER_MODE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --secret)
      SECRET="${2:-}"
      shift 2
      ;;
    --branch)
      DEFAULT_BRANCH="${2:-}"
      shift 2
      ;;
    --test-command)
      TEST_COMMAND="${2:-}"
      shift 2
      ;;
    --api-port)
      API_PORT="${2:-}"
      shift 2
      ;;
    --enable-escalation)
      ENABLE_ESCALATION="${2:-}"
      shift 2
      ;;
    --root)
      TARGET_ROOT="${2:-}"
      shift 2
      ;;
    --danger-mode)
      DANGER_MODE="true"
      shift 1
      ;;
    -h|--help)
      echo "Usage: bash scripts/setup_local_config.sh [options]"
      echo ""
      echo "Options:"
      echo "  --repo <owner/repo>              default: manbalboy/agent-hub"
      echo "  --secret <webhook_secret>        default: auto-generate random secret"
      echo "  --branch <branch_name>           default: main"
      echo "  --test-command <shell_command>   default: echo skip tests"
      echo "  --api-port <port_number>         default: 8321"
      echo "  --enable-escalation <true|false> default: false"
      echo "  --root <path>                    default: /home/docker/agentHub"
      echo "  --danger-mode                    opt-in: codex 우회 플래그를 생성 명령에 추가"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Run with --help for available options."
      exit 1
      ;;
  esac
done

if [[ -z "$REPO" ]]; then
  echo "Missing --repo value (example: myname/myrepo)"
  exit 1
fi

if ! [[ "$API_PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid --api-port value: $API_PORT"
  exit 1
fi

if [[ -z "$SECRET" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    SECRET="$(openssl rand -hex 32)"
  else
    # Fallback when openssl is unavailable.
    SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  fi
fi

PLANNER_BIN="$(command -v gemini || true)"
CODER_BIN="$(command -v codex || true)"
ESCALATION_BIN="$(command -v claude || true)"
NODE_BIN="$(command -v node || true)"

if [[ -z "$PLANNER_BIN" ]]; then
  PLANNER_BIN="gemini"
fi
if [[ -z "$CODER_BIN" ]]; then
  CODER_BIN="codex"
fi
if [[ -z "$ESCALATION_BIN" ]]; then
  ESCALATION_BIN="claude"
fi

CODER_FLAGS=(exec - -C '{work_dir}' --color never)
if [[ "$DANGER_MODE" == "true" ]]; then
  CODER_FLAGS=(exec - --dangerously-bypass-approvals-and-sandbox -C '{work_dir}' --color never)
fi

PLANNER_COMMAND="cat {prompt_file} | $PLANNER_BIN -p '' --approval-mode yolo --model gemini-3.1-pro-preview --output-format text > {plan_path}"
CODER_COMMAND="cat {prompt_file} | $CODER_BIN ${CODER_FLAGS[*]}"
REVIEWER_COMMAND="cat {prompt_file} | $PLANNER_BIN -p '' --approval-mode yolo --model gemini-3.1-pro-preview --output-format text > {review_path}"

if [[ -n "$NODE_BIN" && "$PLANNER_BIN" != "gemini" ]]; then
  PLANNER_COMMAND="cat {prompt_file} | $NODE_BIN $PLANNER_BIN -p '' --approval-mode yolo --model gemini-3.1-pro-preview --output-format text > {plan_path}"
  REVIEWER_COMMAND="cat {prompt_file} | $NODE_BIN $PLANNER_BIN -p '' --approval-mode yolo --model gemini-3.1-pro-preview --output-format text > {review_path}"
fi

if [[ -n "$NODE_BIN" && "$CODER_BIN" != "codex" ]]; then
  CODER_COMMAND="cat {prompt_file} | $NODE_BIN $CODER_BIN ${CODER_FLAGS[*]}"
fi

mkdir -p "$TARGET_ROOT/config" "$TARGET_ROOT/data/logs" "$TARGET_ROOT/workspaces"

cat > "$TARGET_ROOT/.env" <<EOF
AGENTHUB_WEBHOOK_SECRET=$SECRET
AGENTHUB_ALLOWED_REPOSITORY=$REPO

AGENTHUB_DATA_DIR=$TARGET_ROOT/data
AGENTHUB_WORKSPACE_DIR=$TARGET_ROOT/workspaces
AGENTHUB_COMMAND_CONFIG=$TARGET_ROOT/config/ai_commands.json

AGENTHUB_MAX_RETRIES=3
AGENTHUB_TEST_COMMAND="$TEST_COMMAND"
AGENTHUB_DEFAULT_BRANCH=$DEFAULT_BRANCH
AGENTHUB_WORKER_POLL_SECONDS=5
AGENTHUB_ENABLE_ESCALATION=$ENABLE_ESCALATION
AGENTHUB_API_PORT=$API_PORT
EOF

cat > "$TARGET_ROOT/config/ai_commands.json" <<EOF
{
  "planner": "$PLANNER_COMMAND",
  "coder": "$CODER_COMMAND",
  "reviewer": "$REVIEWER_COMMAND",
  "escalation": "cat {prompt_file} | $ESCALATION_BIN --print > ESCALATION.md"
}
EOF

cat > "$TARGET_ROOT/config/apps.json" <<EOF
[
  {
    "code": "default",
    "name": "Default",
    "repository": "$REPO"
  }
]
EOF

cat > "$TARGET_ROOT/.webhook_secret.txt" <<EOF
$SECRET
EOF

echo "[OK] Wrote $TARGET_ROOT/.env"
echo "[OK] Wrote $TARGET_ROOT/config/ai_commands.json"
echo "[OK] Wrote $TARGET_ROOT/config/apps.json"
echo "[OK] Wrote $TARGET_ROOT/.webhook_secret.txt"
echo ""
echo "Use this value in GitHub Webhook Secret:"
echo "$SECRET"
echo ""
echo "Current settings:"
echo "  AGENTHUB_ALLOWED_REPOSITORY=$REPO"
echo "  AGENTHUB_DEFAULT_BRANCH=$DEFAULT_BRANCH"
echo "  AGENTHUB_TEST_COMMAND=$TEST_COMMAND"
echo "  AGENTHUB_API_PORT=$API_PORT"
echo "  DANGER_MODE=$DANGER_MODE"
if [[ "$DANGER_MODE" != "true" ]]; then
  echo "[NOTE] 기본 생성값은 안전 모드입니다. 자동 우회가 필요할 때만 --danger-mode 를 명시적으로 사용하세요."
fi
echo "[NEXT] If needed, review CLI command options in ai_commands.json for your installed versions."
