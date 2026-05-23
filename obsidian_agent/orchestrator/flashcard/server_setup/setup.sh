#!/bin/bash
# setup.sh — Flashcard server setup for Dedalus Machine
# Uses venv (official PEP 668 way) since Ubuntu 24.04 blocks system-wide pip.

set -euo pipefail

APP_DIR="/home/machine/flashcard_app"
LOG_FILE="$APP_DIR/server.log"
PORT=8000

echo "========================================"
echo "[setup] Flashcard Server Setup (venv + pip)"
echo "========================================"

# --- Initial resource check ---
echo "[setup] Initial disk:"
df -h /
echo "[setup] Initial memory:"
free -h 2>/dev/null || cat /proc/meminfo | head -n 4

# --- 1. Install system packages ---
echo "[setup] Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_SUSPEND=1
apt-get update -qq
echo "[setup] apt-get update done"
apt-get install -y -qq --no-install-recommends python3-full
echo "[setup] System packages installed"

python3 --version

# --- 2. Create app directory ---
mkdir -p "$APP_DIR"

# --- 3. Verify server.py exists ---
if [ ! -f "$APP_DIR/server.py" ]; then
    echo "[setup] ERROR: server.py not found"
    exit 1
fi
echo "[setup] server.py found"

# --- 4. Create Python venv ---
echo "[setup] Creating Python venv..."
cd "$APP_DIR"
python3 -m venv .venv
echo "[setup] Venv created"

# --- 5. Install Python packages inside venv ---
echo "[setup] Installing Python packages (inside venv)..."
.venv/bin/pip install --no-cache-dir -q \
    fastapi uvicorn requests genanki
echo "[setup] Python packages installed"

# --- 6. Kill any stale processes ---
echo "[setup] Checking for stale server processes..."
pkill -f 'uvicorn.*server:app' || true

# --- 7. Start FastAPI server inside venv ---
echo "[setup] Starting FastAPI server..."
export DEDALUS_API_KEY="$DEDALUS_API_KEY"
nohup .venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port "$PORT" > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "[setup] Server PID: $SERVER_PID"
sleep 3

# --- 8. Health check ---
echo "[setup] Waiting for server health check..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1; then
        echo "[setup] Server is running on port $PORT"
        break
    fi
    sleep 1
done

if ! curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1; then
    echo "[setup] ERROR: Server failed to start"
    echo "[setup] Server log:"
    tail -30 "$LOG_FILE" || echo "(no log file)"
    exit 1
fi

# --- 9. Final resource usage ---
echo "========================================"
echo "[setup] Final disk usage:"
df -h /
echo "[setup] Final memory usage:"
free -h 2>/dev/null || cat /proc/meminfo | head -n 4
echo "========================================"
echo "[setup] Done! Server is ready."
