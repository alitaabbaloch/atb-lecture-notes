#!/bin/zsh
# ATB Lecture Notes: start the server by hand (fallback when the extension cannot start it).

REPO="${0:A:h}"
APP_DIR="${ATB_APP_DIR:-$HOME/Library/Application Support/ATB Lecture Notes}"

PORT="${ATB_PORT:-}"
if [[ -z "$PORT" && -f "$APP_DIR/config.json" ]]; then
  PORT="$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$APP_DIR/config.json" | grep -o '[0-9]*$' | head -n 1)"
fi
PORT="${PORT:-8765}"

if [[ "$(curl -s --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)" == *'"atb-lecture-notes"'* ]]; then
  echo "The server is already running on port $PORT. Nothing to do."
  exit 0
fi

if [[ ! -x "$REPO/.venv/bin/python" ]]; then
  echo "Not installed yet. Run install/install-mac.command first."
  exit 1
fi

echo "Starting the server on http://127.0.0.1:$PORT. Close this window or press Ctrl+C to stop it."
cd "$REPO"
export ATB_PORT="$PORT"
exec .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port "$PORT" --app-dir server
