#!/bin/zsh
# ATB Lecture Notes: optional second hearing (Whisper large-v3). Safe to run more than once.
# Test overrides: ATB_APP_DIR, ATB_SKIP_MODEL=1

set -u

REPO="${0:A:h:h}"
APP_DIR="${ATB_APP_DIR:-$HOME/Library/Application Support/ATB Lecture Notes}"
MODEL_REPO="mlx-community/whisper-large-v3-mlx"

say() { print -r -- "$*"; }
step() { print -r -- ""; print -r -- "==> $*"; }
fail() { print -r -- ""; print -r -- "Stopped: $*"; exit 1; }

say "ATB Lecture Notes: second hearing"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
command -v uv >/dev/null 2>&1 || fail "uv is missing. Run install/install-mac.command first."

# 1. What this does
step "What this does"
say "It downloads Whisper large-v3 (about 3 GB). Every Urdu lesson is then transcribed a second time."
say "The second pass fills holes in the transcript and helps an AI that writes notes later."
say "Lessons take about 10 minutes longer to finish in the background."

# 2. Model
step "Checking the Whisper model"
HF_HUB="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
MODEL_DIR="$HF_HUB/models--${MODEL_REPO//\//--}/snapshots"
if [[ "${ATB_SKIP_MODEL:-0}" == "1" ]]; then
  say "Skipped (ATB_SKIP_MODEL=1)."
elif [[ -d "$MODEL_DIR" && -n "$(ls -A "$MODEL_DIR" 2>/dev/null)" ]]; then
  say "The Whisper model is already downloaded."
else
  say "Downloading $MODEL_REPO. This can take a while."
  (cd "$REPO" && uv run --no-dev python -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL_REPO')") \
    || fail "the model download failed. Check your internet connection and run this again."
  say "Model downloaded."
fi

# 3. Switch it on
step "Switching the second hearing on"
[[ -f "$APP_DIR/config.json" ]] || fail "there is no settings file at $APP_DIR/config.json. Run install/install-mac.command first."
PYTHON="$REPO/.venv/bin/python"   # the app's own Python: a Mac without developer tools has no working python3
[[ -x "$PYTHON" ]] || PYTHON="python3"
"$PYTHON" - "$APP_DIR/config.json" <<'PY' || fail "could not update $APP_DIR/config.json. It was not changed."
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except ValueError:
    sys.exit(1)
if not isinstance(data, dict):
    sys.exit(1)
data["second_hearing"] = True
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY
say "Done. Settings updated: $APP_DIR/config.json"
say "Restart the server for this to take effect: stop it and start it again from the extension."
