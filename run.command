#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-powerrom}"
CONDA_BIN="${CONDA_BIN:-$(command -v conda || true)}"

echo "PowerROM local launcher"
echo "Repo: $ROOT_DIR"

if ! command -v npm >/dev/null 2>&1; then
  echo "Missing npm"
  exit 1
fi

if [ -z "$CONDA_BIN" ]; then
  echo "Missing conda"
  exit 1
fi

CONDA_BASE="$("$CONDA_BIN" info --base)"

if ! "$CONDA_BIN" env list | awk '{print $1}' | grep -qx "$CONDA_ENV_NAME"; then
  echo "Creating Conda environment: $CONDA_ENV_NAME"
  "$CONDA_BIN" create -n "$CONDA_ENV_NAME" python=3.12 -y
fi

echo "Installing backend dependencies..."
echo "Using Conda environment: $CONDA_ENV_NAME"
"$CONDA_BIN" run -n "$CONDA_ENV_NAME" python -m pip install --upgrade pip >/dev/null
"$CONDA_BIN" run -n "$CONDA_ENV_NAME" python -m pip install -r "$ROOT_DIR/requirements.txt"

echo "Installing frontend dependencies..."
npm --prefix "$ROOT_DIR/frontend" install

cleanup() {
  if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${FRONTEND_PID:-}" ] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting backend on http://localhost:$BACKEND_PORT"
(
  cd "$ROOT_DIR"
  source "$CONDA_BASE/etc/profile.d/conda.sh"
  conda activate "$CONDA_ENV_NAME"
  exec uvicorn backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload
) &
BACKEND_PID=$!

sleep 2

echo "Starting frontend on http://localhost:$FRONTEND_PORT"
echo "Backend PID: $BACKEND_PID"
echo "Press Ctrl+C to stop both servers."
(
  cd "$ROOT_DIR/frontend"
  NEXT_PUBLIC_API_BASE_URL="http://localhost:$BACKEND_PORT/api" exec npm run dev -- --webpack --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

for _ in {1..40}; do
  if curl -fsS "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1; then
    open "http://localhost:$FRONTEND_PORT"
    break
  fi
  sleep 0.5
done

wait "$FRONTEND_PID"
