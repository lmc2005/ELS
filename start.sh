#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== English Learning System ==="
echo ""

# Backend
echo "[1/2] Starting backend (FastAPI + Uvicorn)..."
cd "$SCRIPT_DIR/backend"
if [ ! -d "venv" ]; then
    python3.12 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt -q
else
    source venv/bin/activate
fi
pip uninstall -y hf-xet >/dev/null 2>&1 || true
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
echo "  Backend: http://127.0.0.1:8000 (PID $BACKEND_PID)"

# Frontend
echo "[2/2] Starting frontend (Vite + React)..."
cd "$SCRIPT_DIR/frontend"
if [ ! -d "node_modules" ]; then
    npm install --silent
fi
npx vite --host 127.0.0.1 --port 5173 &
FRONTEND_PID=$!
echo "  Frontend: http://127.0.0.1:5173 (PID $FRONTEND_PID)"

echo ""
echo "=== Ready ==="
echo "Open http://127.0.0.1:5173 in your browser."
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
