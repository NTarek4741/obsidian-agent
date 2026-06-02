#!/bin/bash
# Podcast server setup for a fresh Dedalus Machine.
# Uses a venv since Ubuntu 24.04 blocks system-wide pip (PEP 668).

set -euo pipefail

APP_DIR="/home/machine/podcast_app"
LOG_FILE="$APP_DIR/server.log"
PORT=8000

echo "Podcast server setup"

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
apt-get install -y -qq --no-install-recommends python3-full espeak-ng libsndfile1

# 2. Create venv, install Python packages.
echo "Creating venv and installing Python packages..."
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir -q fastapi uvicorn kokoro soundfile numpy dedalus-labs

# 3. Pre-load Kokoro so the first /generate-podcast request isn't slow.
echo "Pre-loading Kokoro model (1-2 minutes)..."
.venv/bin/python -c "
from kokoro import KPipeline
pipeline = KPipeline(lang_code='a')
for _, _, audio in pipeline('Hello world', voice='af_bella'):
    if audio is not None:
        print(f'Generated {len(audio)} samples — Kokoro ready')
        break
"

# 4. Start uvicorn in the background.
echo "Starting FastAPI server..."
nohup .venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port "$PORT" > "$LOG_FILE" 2>&1 &
echo "Server PID: $!"

# 5. Poll /health for 30s; tail server.log and exit 1 on failure.
echo "Waiting for server health check..."
for i in $(seq 1 30); do
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
