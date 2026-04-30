#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-}"
CONDA_BIN="${CONDA_BIN:-$(command -v conda || true)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "PowerROM local launcher"
echo "Repo: $ROOT_DIR"

if ! command -v npm >/dev/null 2>&1; then
  echo "Missing npm"
  exit 1
fi

USE_CONDA=0
CONDA_BASE=""
if [ -n "$CONDA_ENV_NAME" ] && [ -n "$CONDA_BIN" ]; then
  CONDA_BASE="$("$CONDA_BIN" info --base)"
  USE_CONDA=1
fi

if [ "$USE_CONDA" -eq 0 ] && ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Missing Python interpreter: $PYTHON_BIN"
  exit 1
fi

echo "Installing backend dependencies..."
if [ "$USE_CONDA" -eq 1 ]; then
  echo "Using Conda environment: $CONDA_ENV_NAME"
  "$CONDA_BIN" run -n "$CONDA_ENV_NAME" python -m pip install --upgrade pip >/dev/null
  "$CONDA_BIN" run -n "$CONDA_ENV_NAME" python -m pip install -r "$ROOT_DIR/requirements.txt"
else
  echo "Using system Python: $PYTHON_BIN"
  "$PYTHON_BIN" -m pip install --upgrade pip >/dev/null
  "$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt"
fi

echo "Installing frontend dependencies..."
npm --prefix "$ROOT_DIR/frontend" install

FRONTEND_CMD="cd \"$ROOT_DIR/frontend\" && NEXT_PUBLIC_API_BASE_URL=\"http://localhost:$BACKEND_PORT/api\" npm run dev -- --port $FRONTEND_PORT"

if [ "$USE_CONDA" -eq 1 ]; then
  BACKEND_CMD="cd \"$ROOT_DIR\" && source \"$CONDA_BASE/etc/profile.d/conda.sh\" && conda activate \"$CONDA_ENV_NAME\" && uvicorn backend.main:app --host 0.0.0.0 --port $BACKEND_PORT --reload"
else
  BACKEND_CMD="cd \"$ROOT_DIR\" && $PYTHON_BIN -m uvicorn backend.main:app --host 0.0.0.0 --port $BACKEND_PORT --reload"
fi

echo "Starting backend on http://localhost:$BACKEND_PORT"
osascript <<EOF
tell application "Terminal"
  activate
  do script "$BACKEND_CMD"
end tell
EOF

sleep 1

echo "Starting frontend on http://localhost:$FRONTEND_PORT"
osascript <<EOF
tell application "Terminal"
  activate
  do script "$FRONTEND_CMD"
end tell
EOF

echo "Launched:"
echo "  Backend:  http://localhost:$BACKEND_PORT"
echo "  Frontend: http://localhost:$FRONTEND_PORT"
