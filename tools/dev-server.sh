#!/bin/bash
# Starts the server for development on port 8766 with throwaway folders.
# Usage: tools/dev-server.sh [fake|mlx]     (default: fake)
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${TMPDIR:-/tmp}/atb-lecture-notes-dev"
mkdir -p "$BASE/app" "$BASE/data"
export ATB_APP_DIR="$BASE/app" ATB_DATA_DIR="$BASE/data" ATB_PORT=8766 ATB_DEV=1
export ATB_ENGINE="${1:-fake}" ATB_NOTES="${ATB_NOTES:-fake}"
echo "dev server on http://127.0.0.1:8766/dev/  (engine $ATB_ENGINE, data in $BASE)"
exec uv run uvicorn server:app --host 127.0.0.1 --port 8766 --app-dir server
