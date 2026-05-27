#!/bin/bash
# setup.sh — Clean podcast server setup for Dedalus Machine
# Uses venv (official PEP 668 way) since Ubuntu 24.04 blocks system-wide pip.

set -euo pipefail

APP_DIR="/home/machine/podcast_app"
LOG_FILE="$APP_DIR/server.log"
PORT=8000

echo "========================================"
echo "[setup] Clean Setup (venv + pip)"
echo "========================================"

# --- 1. Install system packages ---
echo "[setup] Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_SUSPEND=1
apt-get update -qq
echo "[setup] apt-get update done"
apt-get install -y -qq --no-install-recommends python3-full ffmpeg espeak-ng libsndfile1
echo "[setup] System packages installed"

# Verify tools
python3 --version
ffmpeg -version | head -n 1
espeak-ng --version | head -n 1

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
    fastapi uvicorn kokoro soundfile numpy requests
echo "[setup] Python packages installed"

# --- 6. Pre-load Kokoro model (download weights now) ---
echo "[setup] Pre-loading Kokoro model (this may take 1-2 minutes)..."
.venv/bin/python -c "
import traceback
try:
    from kokoro import KPipeline
    print('[setup] Loading Kokoro pipeline...')
    pipeline = KPipeline(lang_code='a')
    for _, _, audio in pipeline('Hello world', voice='af_bella'):
        if audio is not None:
            print(f'[setup] Generated {len(audio)} samples — Kokoro ready')
            break
    print('[setup] Kokoro model loaded successfully')
except Exception as exc:
    print(f'[setup] ERROR: Kokoro pre-load failed: {exc}')
    traceback.print_exc()
    exit(1)
"
echo "[setup] Kokoro model ready"

# --- 7. Kill any stale processes ---
echo "[setup] Checking for stale server processes..."
pkill -f 'uvicorn.*server:app' || true

# --- 8. Start FastAPI server inside venv ---
echo "[setup] Starting FastAPI server..."
export DEDALUS_API_KEY="$DEDALUS_API_KEY"
nohup .venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port "$PORT" > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "[setup] Server PID: $SERVER_PID"
sleep 3

# --- 9. Health check ---
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

echo "[setup] Done! Server is ready."
