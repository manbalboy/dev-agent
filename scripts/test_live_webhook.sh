#!/usr/bin/env bash
set -euo pipefail

# End-to-end live webhook test helper.
#
# What it validates:
# 1) API health endpoint responds
# 2) Signed GitHub issues webhook is accepted
# 3) Worker consumes queue and updates job status to done/failed
#
# Usage examples:
#   bash scripts/test_live_webhook.sh --issue 123
#   bash scripts/test_live_webhook.sh --issue 123 --timeout 600 --base-url http://127.0.0.1:8321

ROOT_DIR="/home/docker/agentHub"
ISSUE_NUMBER=""
TIMEOUT_SECONDS="300"
POLL_INTERVAL="2"
BASE_URL=""
ENV_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue)
      ISSUE_NUMBER="${2:-}"
      shift 2
      ;;
    --timeout)
      TIMEOUT_SECONDS="${2:-}"
      shift 2
      ;;
    --poll)
      POLL_INTERVAL="${2:-}"
      shift 2
      ;;
    --base-url)
      BASE_URL="${2:-}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    --root)
      ROOT_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: bash scripts/test_live_webhook.sh --issue <number> [options]

Required:
  --issue <number>         Existing issue number in AGENTHUB_ALLOWED_REPOSITORY

Options:
  --timeout <seconds>      Wait timeout for done/failed (default: 300)
  --poll <seconds>         Poll interval (default: 2)
  --base-url <url>         API base URL (default: http://127.0.0.1:$AGENTHUB_API_PORT)
  --env-file <path>        Override env file (default: <root>/.env)
  --root <path>            Project root (default: /home/docker/agentHub)

Exit codes:
  0 -> status became done
  1 -> failed/timeout/invalid response
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Run with --help for usage."
      exit 1
      ;;
  esac
done

if [[ -z "$ISSUE_NUMBER" ]]; then
  echo "--issue is required (example: --issue 123)"
  exit 1
fi

export ISSUE_NUMBER

if ! [[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "--issue must be an integer: $ISSUE_NUMBER"
  exit 1
fi

if ! [[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "--timeout must be an integer: $TIMEOUT_SECONDS"
  exit 1
fi

if ! [[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]]; then
  echo "--poll must be an integer: $POLL_INTERVAL"
  exit 1
fi

if [[ -z "$ENV_FILE" ]]; then
  ENV_FILE="$ROOT_DIR/.env"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

if [[ -z "${AGENTHUB_WEBHOOK_SECRET:-}" ]]; then
  echo "AGENTHUB_WEBHOOK_SECRET is not set in $ENV_FILE"
  exit 1
fi

if [[ -z "${AGENTHUB_ALLOWED_REPOSITORY:-}" ]]; then
  echo "AGENTHUB_ALLOWED_REPOSITORY is not set in $ENV_FILE"
  exit 1
fi

if [[ -z "$BASE_URL" ]]; then
  BASE_URL="http://127.0.0.1:${AGENTHUB_API_PORT:-8321}"
fi

DATA_DIR="${AGENTHUB_DATA_DIR:-$ROOT_DIR/data}"
JOBS_FILE="$DATA_DIR/jobs.json"
LOGS_DIR="$DATA_DIR/logs"

if [[ ! -f "$JOBS_FILE" ]]; then
  echo "jobs file not found: $JOBS_FILE"
  echo "Hint: start API/worker once so runtime files are created."
  exit 1
fi

echo "[1/4] API health check: $BASE_URL/healthz"
HEALTH_BODY="$(curl -sS "$BASE_URL/healthz")"
HEALTH_BODY="$HEALTH_BODY" python3 - <<'PY'
import json,os
try:
  payload=json.loads(os.environ["HEALTH_BODY"])
except json.JSONDecodeError:
    print("[ERROR] /healthz response is not valid JSON")
    raise SystemExit(1)
if payload.get("status")!="ok":
    print(f"[ERROR] API not healthy: {payload}")
    raise SystemExit(1)
print(f"[OK] API healthy. allowed_repository={payload.get('allowed_repository')}")
PY

PAYLOAD_JSON="$(python3 - <<'PY'
import json,os
repo=os.environ["AGENTHUB_ALLOWED_REPOSITORY"]
issue=int(os.environ["ISSUE_NUMBER"])
payload={
  "action":"labeled",
  "label":{"name":"agent:run"},
  "repository":{"full_name":repo},
  "issue":{
    "number":issue,
    "title":f"Live webhook test issue #{issue}",
    "html_url":f"https://github.com/{repo}/issues/{issue}"
  }
}
print(json.dumps(payload,separators=(",",":")))
PY
)"

export PAYLOAD_JSON

SIGNATURE="$(python3 - <<'PY'
import hashlib,hmac,os
secret=os.environ["AGENTHUB_WEBHOOK_SECRET"].encode("utf-8")
body=os.environ["PAYLOAD_JSON"].encode("utf-8")
digest=hmac.new(secret,body,hashlib.sha256).hexdigest()
print(f"sha256={digest}")
PY
)"

echo "[2/4] Send signed webhook"
RESPONSE_FILE="$(mktemp)"
HTTP_CODE="$(curl -sS -o "$RESPONSE_FILE" -w "%{http_code}" -X POST "$BASE_URL/webhooks/github" \
  -H "X-GitHub-Event: issues" \
  -H "X-Hub-Signature-256: $SIGNATURE" \
  -H "Content-Type: application/json" \
  --data "$PAYLOAD_JSON")"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "[ERROR] webhook HTTP status: $HTTP_CODE"
  cat "$RESPONSE_FILE"
  rm -f "$RESPONSE_FILE"
  exit 1
fi

WEBHOOK_BODY="$(cat "$RESPONSE_FILE")"
rm -f "$RESPONSE_FILE"
echo "[INFO] webhook response: $WEBHOOK_BODY"

JOB_ID="$(WEBHOOK_BODY="$WEBHOOK_BODY" python3 - <<'PY'
import json,os
body=os.environ["WEBHOOK_BODY"]
payload=json.loads(body)
if not payload.get("accepted"):
    print("", end="")
else:
    print(payload.get("job_id", ""), end="")
PY
)"

if [[ -z "$JOB_ID" ]]; then
  echo "[ERROR] webhook was not accepted. Check response above."
  exit 1
fi

echo "[OK] accepted job_id=$JOB_ID"

echo "[3/4] Wait for job status (timeout=${TIMEOUT_SECONDS}s)"
START_TS="$(date +%s)"
FINAL_STATUS=""
FINAL_STAGE=""
FINAL_ERROR=""

while true; do
  NOW_TS="$(date +%s)"
  ELAPSED=$((NOW_TS - START_TS))

  CURRENT_ROW="$(JOBS_FILE="$JOBS_FILE" JOB_ID="$JOB_ID" python3 - <<'PY'
import json,os
jobs_file=os.environ["JOBS_FILE"]
job_id=os.environ["JOB_ID"]
with open(jobs_file,"r",encoding="utf-8") as f:
    data=json.load(f)
item=data.get(job_id,{})
status=item.get("status","")
stage=item.get("stage","")
error=(item.get("error_message") or "").replace("\n"," ")
print(f"{status}\t{stage}\t{error}")
PY
)"

  CURRENT_STATUS="${CURRENT_ROW%%$'\t'*}"
  REST_ROW="${CURRENT_ROW#*$'\t'}"
  CURRENT_STAGE="${REST_ROW%%$'\t'*}"
  CURRENT_ERROR="${REST_ROW#*$'\t'}"

  echo "  - ${ELAPSED}s status=${CURRENT_STATUS:-unknown} stage=${CURRENT_STAGE:-unknown}"

  if [[ "$CURRENT_STATUS" == "done" || "$CURRENT_STATUS" == "failed" ]]; then
    FINAL_STATUS="$CURRENT_STATUS"
    FINAL_STAGE="$CURRENT_STAGE"
    FINAL_ERROR="$CURRENT_ERROR"
    break
  fi

  if (( ELAPSED >= TIMEOUT_SECONDS )); then
    echo "[ERROR] timeout waiting for done/failed status"
    exit 1
  fi

  sleep "$POLL_INTERVAL"
done

echo "[4/4] Result"
echo "job_id=$JOB_ID"
echo "status=$FINAL_STATUS"
echo "stage=$FINAL_STAGE"

if [[ "$FINAL_STATUS" == "done" ]]; then
  echo "[SUCCESS] Live flow verified."
  exit 0
fi

echo "[FAILED] Job finished with failed status"
if [[ -n "$FINAL_ERROR" ]]; then
  echo "error=$FINAL_ERROR"
fi
LOG_PATH="$LOGS_DIR/${JOB_ID}.log"
if [[ -f "$LOG_PATH" ]]; then
  echo "last log lines ($LOG_PATH):"
  tail -n 40 "$LOG_PATH"
else
  echo "log file not found: $LOG_PATH"
fi
exit 1
