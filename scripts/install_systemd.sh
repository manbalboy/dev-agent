#!/usr/bin/env bash
set -euo pipefail

# Install and start AgentHub systemd services.
# Run with sudo:
#   sudo bash scripts/install_systemd.sh

TARGET_ROOT="/home/docker/agentHub"
SERVICE_USER="docker"
API_PORT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      TARGET_ROOT="${2:-}"
      shift 2
      ;;
    --user)
      SERVICE_USER="${2:-}"
      shift 2
      ;;
    --port)
      API_PORT="${2:-}"
      shift 2
      ;;
    -h|--help)
      echo "Usage: sudo bash scripts/install_systemd.sh [options]"
      echo ""
      echo "Options:"
      echo "  --root <path>   default: /home/docker/agentHub"
      echo "  --user <name>   default: docker"
      echo "  --port <num>    default: read AGENTHUB_API_PORT from .env, fallback 8321"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Run with --help for available options."
      exit 1
      ;;
  esac
done

if [[ -z "$API_PORT" ]]; then
  if [[ -f "$TARGET_ROOT/.env" ]]; then
    API_PORT="$(grep -E '^AGENTHUB_API_PORT=' "$TARGET_ROOT/.env" | tail -n 1 | cut -d= -f2- || true)"
  fi
fi

if [[ -z "$API_PORT" ]]; then
  API_PORT="8321"
fi

if ! [[ "$API_PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid API port: $API_PORT"
  exit 1
fi

cat > /etc/systemd/system/agenthub-api.service <<EOF
[Unit]
Description=AgentHub FastAPI API server
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$TARGET_ROOT
EnvironmentFile=$TARGET_ROOT/.env
ExecStart=$TARGET_ROOT/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $API_PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/agenthub-worker.service <<EOF
[Unit]
Description=AgentHub orchestration worker
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$TARGET_ROOT
EnvironmentFile=$TARGET_ROOT/.env
ExecStart=$TARGET_ROOT/.venv/bin/python -m app.worker_main
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now agenthub-api
systemctl enable --now agenthub-worker

systemctl --no-pager --full status agenthub-api | head -n 20 || true
systemctl --no-pager --full status agenthub-worker | head -n 20 || true

echo "[OK] systemd services installed and started."
echo "[INFO] AgentHub API port is set to $API_PORT"
