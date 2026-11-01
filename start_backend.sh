#!/usr/bin/env bash
# macOS / Linux counterpart of start_backend.bat.
# Starts the gateway on ws://127.0.0.1:19876, waits until the port answers,
# then holds the terminal until a key is pressed and shuts the backend down.
set -u

cd "$(dirname "$0")" || exit 1

PORT=19876
PY=""
BACKEND_PID=""

port_open() {
    "$PY" - "$PORT" <<'PYEOF'
import socket, sys
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PYEOF
}

cleanup() {
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "Stopping backend..."
        kill "$BACKEND_PID" 2>/dev/null
        for _ in $(seq 1 20); do
            kill -0 "$BACKEND_PID" 2>/dev/null || break
            sleep 0.3
        done
        kill -9 "$BACKEND_PID" 2>/dev/null
        wait "$BACKEND_PID" 2>/dev/null
    fi
    BACKEND_PID=""
}
trap cleanup EXIT INT TERM

echo "========================================"
echo "  PebbleAgent Backend - starting..."
echo "========================================"
echo

echo "[1/3] Checking environment..."
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "  [ERROR] python3 was not found in PATH."
    echo "  Install Python 3.11+ and make sure it is on PATH."
    exit 1
fi

if ! "$PY" -c "import websockets, openai, httpx, yaml" >/dev/null 2>&1; then
    echo "  [ERROR] Python dependencies are not installed."
    echo "  Run from the project root:"
    echo "    $PY -m pip install -r requirements.txt"
    exit 1
fi

echo "  OK - environment ready ($PY)"
echo

echo "[2/3] Cleaning up stale processes..."
if command -v lsof >/dev/null 2>&1; then
    STALE="$(lsof -ti "tcp:$PORT" 2>/dev/null)"
    if [ -n "$STALE" ]; then
        echo "      found PID=$STALE, terminating..."
        # shellcheck disable=SC2086
        kill $STALE 2>/dev/null
        sleep 1
        STALE="$(lsof -ti "tcp:$PORT" 2>/dev/null)"
        if [ -n "$STALE" ]; then
            # shellcheck disable=SC2086
            kill -9 $STALE 2>/dev/null
        fi
    fi
else
    echo "      lsof unavailable, skipping (a stale listener would fail to bind)"
fi
sleep 1

echo "[3/3] Starting backend..."
"$PY" run_backend.py &
BACKEND_PID=$!

echo "      waiting for ws://127.0.0.1:$PORT ..."
READY=0
for _ in $(seq 1 60); do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo
        echo "  [ERROR] backend exited before the port opened."
        BACKEND_PID=""
        exit 1
    fi
    if port_open; then
        READY=1
        break
    fi
    sleep 1
done

if [ "$READY" -ne 1 ]; then
    echo
    echo "  [ERROR] the port did not open within 60s."
    exit 1
fi

echo
echo "========================================"
echo "  Backend is ready"
echo "  ws://127.0.0.1:$PORT"
echo
echo "  Press any key to stop the backend"
echo "========================================"
echo

# One key, no Enter: mirrors the `pause` in the batch file.
if [ -t 0 ]; then
    read -r -n 1 -s _ || true
else
    read -r _ || true
fi
echo
