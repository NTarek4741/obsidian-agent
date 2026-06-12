#!/bin/bash
# Utility server setup for a fresh Dedalus Machine.
# Hosts the utility agents (transcript cleanup, fast/deep research, mind map,
# chat) against a persistent vault at /home/machine/vault. The deployed bundle
# is fully self-contained: server.py + runner.py + tools.py + vault_sync.py +
# prompts/. Input pipelines (captions, whisper) run on the local client.

set -euo pipefail

APP_DIR="/home/machine/utility_app"
VAULT_DIR="${OBSIDIAN_VAULT_PATH:-/home/machine/vault}"
LOG_FILE="$APP_DIR/server.log"
PORT=8000

echo "Utility server setup"

# 1. Wait for the dpkg lock, then apt-get install system dependencies.
#    Fresh Dedalus VMs boot with unattended-upgrades still running, holding
#    the lock for 1-3 minutes. Without this wait, apt-get races and fails.
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_SUSPEND=1

echo "Waiting for dpkg lock to be free (up to 300s)..."
WAIT_START=$(date +%s)
while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock >/dev/null 2>&1; do
    ELAPSED=$(( $(date +%s) - WAIT_START ))
    if [ "$ELAPSED" -ge 300 ]; then
        echo "WARN: dpkg lock still held after 300s — proceeding anyway"
        break
    fi
    echo "  still locked (t+${ELAPSED}s)..."
    sleep 5
done

echo "Installing system packages..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3-full

# 2. Persistent vault for the agents (survives jobs and autosleep).
mkdir -p "$VAULT_DIR"

# 3. Create venv, install Python packages.
echo "Creating venv and installing Python packages..."
cd "$APP_DIR"
rm -rf "$APP_DIR/src"   # legacy project-source tree from the old deploy layout
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir -q \
    fastapi uvicorn pydantic pyyaml \
    python-multipart dedalus-labs

# 4. Install uvicorn as a systemd service so it auto-restarts on every VM
#    boot (including post-wake). Secrets live in a separate EnvironmentFile.
echo "Installing utility-server.service..."
{
    echo "DEDALUS_API_KEY=${DEDALUS_API_KEY}"
    echo "OBSIDIAN_VAULT_PATH=${VAULT_DIR}"
} > /etc/utility-server.env
chmod 600 /etc/utility-server.env

cat >/etc/systemd/system/utility-server.service <<'SYSTEMD_UNIT'
[Unit]
Description=Utility agents FastAPI server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/machine/utility_app
EnvironmentFile=/etc/utility-server.env
ExecStart=/home/machine/utility_app/.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=2
StandardOutput=append:/home/machine/utility_app/server.log
StandardError=append:/home/machine/utility_app/server.log

[Install]
WantedBy=multi-user.target
SYSTEMD_UNIT

systemctl daemon-reload
systemctl enable --now utility-server
echo "utility-server.service enabled and started"

# 5. Poll /health for 60s (first import is heavy); tail log and fail loudly.
echo "Waiting for server health check..."
for i in $(seq 1 60); do
    if curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1; then
        echo "Server is running on port $PORT"
        echo "Done!"
        exit 0
    fi
    sleep 1
done

echo "ERROR: Server failed to start"
tail -30 "$LOG_FILE" || echo "(no log file)"
exit 1
