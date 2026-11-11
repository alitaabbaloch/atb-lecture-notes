#!/bin/zsh
# ATB Lecture Notes: Mac installer (Apple Silicon). Safe to run more than once.
# Test overrides: ATB_APP_DIR, ATB_NM_ROOT, ATB_SKIP_MODEL=1, ATB_SKIP_SYNC=1,
#   ATB_ROMAN_URL_BASE (where to download the speech model from),
#   ATB_CONVERT_CMD (runs instead of the real model conversion, gets the two folders as arguments)

set -u

REPO="${0:A:h:h}"
APP_DIR="${ATB_APP_DIR:-$HOME/Library/Application Support/ATB Lecture Notes}"
NM_ROOT="${ATB_NM_ROOT:-$HOME/Library/Application Support}"
HOST_NAME="com.atb.lecture_notes"
ROMAN_REPO="Oriserve/Whisper-Hindi2Hinglish-Apex"
ROMAN_REV="f3214eed20b4e4d4144e739982d911f87b9cb223"
ROMAN_BASE="${ATB_ROMAN_URL_BASE:-https://huggingface.co/$ROMAN_REPO/resolve/$ROMAN_REV}"
ROMAN_DIR="$APP_DIR/models/roman-urdu"

say() { print -r -- "$*"; }
step() { print -r -- ""; print -r -- "==> $*"; }
fail() { print -r -- ""; print -r -- "Stopped: $*"; exit 1; }

say "ATB Lecture Notes installer"

# 1. Platform
step "Checking this is an Apple Silicon Mac"
[[ "$(uname -s)" == "Darwin" ]] || fail "this installer is for macOS only."
[[ "$(uname -m)" == "arm64" ]] || fail "this installer needs an Apple Silicon Mac (M1 or newer)."
say "OK."

# 2. uv
step "Looking for uv (the Python tool this app uses)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
  say "Found uv at $(command -v uv)."
elif [[ "${ATB_SKIP_SYNC:-0}" == "1" ]]; then
  say "uv not found, skipped because ATB_SKIP_SYNC=1."
else
  if command -v brew >/dev/null 2>&1; then
    say "uv is missing. Installing it with Homebrew."
    brew install uv || fail "Homebrew could not install uv."
  else
    say "uv is missing and Homebrew is not installed. Using the official uv installer."
    curl -LsSf https://astral.sh/uv/install.sh | sh || fail "the uv installer failed."
  fi
  hash -r
  command -v uv >/dev/null 2>&1 || fail "uv was installed but is not on the PATH. Open a new Terminal window and run this again."
fi

# 3. Python packages
step "Installing the Python packages (uv sync --no-dev)"
if [[ "${ATB_SKIP_SYNC:-0}" == "1" ]]; then
  say "Skipped (ATB_SKIP_SYNC=1)."
else
  (cd "$REPO" && uv sync --no-dev) || fail "uv sync failed. Scroll up for the error."
  say "Done."
fi

# 4. App folder and default settings
step "Creating the app folder and default settings"
mkdir -p "$APP_DIR/logs" || fail "could not create $APP_DIR"
if [[ -e "$APP_DIR/config.json" ]]; then
  say "Settings already exist, left unchanged: $APP_DIR/config.json"
else
  cat > "$APP_DIR/config.json" <<'JSON'
{
  "data_dir": null,
  "port": 8765,
  "language": "ur",
  "engine": "auto",
  "model": "auto",
  "transcript": "roman",
  "second_hearing": false,
  "auto_notes": false,
  "stale_session_seconds": 300,
  "allowed_extension_ids": []
}
JSON
  say "Wrote default settings: $APP_DIR/config.json"
fi

# 5. Helper wrapper that Chrome starts
step "Writing the start helper that Chrome calls"
WRAPPER="$APP_DIR/native-host.sh"
printf '#!/bin/sh\nexec "%s/.venv/bin/python" "%s/server/native_host.py"\n' "$REPO" "$REPO" > "$WRAPPER"
chmod +x "$WRAPPER"
say "Wrote $WRAPPER"

# 6. Native messaging manifest for each installed Chromium browser
step "Telling your browsers about the helper"
EXT_ID="$(cd "$REPO" && uv run --no-project python tools/extension_id.py 2>/dev/null)"
if [[ ! "$EXT_ID" =~ '^[a-p]{32}$' ]]; then
  EXT_ID="$(python3 "$REPO/tools/extension_id.py" 2>/dev/null)"
fi
[[ "$EXT_ID" =~ '^[a-p]{32}$' ]] || fail "could not compute the extension ID from $REPO/extension/manifest.json."
say "Extension ID: $EXT_ID"
case "$WRAPPER" in
  *\"*|*\\*) fail "the folder name contains a quote or backslash, which this installer does not support." ;;
esac

found=0
for sub in "Google/Chrome" "Google/Chrome Beta" "Chromium" "BraveSoftware/Brave-Browser" "Microsoft Edge"; do
  if [[ -d "$NM_ROOT/$sub" ]]; then
    dest="$NM_ROOT/$sub/NativeMessagingHosts"
    mkdir -p "$dest" || fail "could not create $dest"
    cat > "$dest/$HOST_NAME.json" <<EOF
{
  "name": "$HOST_NAME",
  "description": "ATB Lecture Notes start helper",
  "path": "$WRAPPER",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXT_ID/"]
}
EOF
    say "Registered for $sub: $dest/$HOST_NAME.json"
    found=1
  fi
done
[[ $found == 1 ]] || say "No Chromium browser folder found under $NM_ROOT. Install Chrome, then run this again."

# 7. Speech model
step "Checking the speech model"
if [[ "${ATB_SKIP_MODEL:-0}" == "1" ]]; then
  say "Skipped (ATB_SKIP_MODEL=1)."
elif [[ -f "$ROMAN_DIR/config.json" && -f "$ROMAN_DIR/weights.safetensors" ]]; then
  say "The speech model is already installed: $ROMAN_DIR"
else
  say "Downloading the Roman Urdu speech model, about 1.6 GB, from its author's page ($ROMAN_REPO)."
  say "Licence: Apache-2.0. If the download stops, run this installer again and it carries on where it stopped."
  DL_DIR="$APP_DIR/models/roman-urdu.download"
  TMP_DIR="$ROMAN_DIR.tmp"
  mkdir -p "$DL_DIR" || fail "could not create $DL_DIR"
  for f in config.json model.safetensors; do
    if [[ -f "$DL_DIR/$f.done" ]]; then
      say "Already downloaded: $f"
      continue
    fi
    curl -L --fail -C - --progress-bar -o "$DL_DIR/$f" "$ROMAN_BASE/$f" \
      || fail "the model download failed. Check your internet connection and run this again. The part already downloaded is kept."
    : > "$DL_DIR/$f.done"
  done
  rm -rf "$TMP_DIR"
  say "Converting the model to the format this app uses."
  if [[ -n "${ATB_CONVERT_CMD:-}" ]]; then
    ${=ATB_CONVERT_CMD} "$DL_DIR" "$TMP_DIR"
  else
    (cd "$REPO" && uv run --no-dev python tools/convert_hf_whisper.py "$DL_DIR" "$TMP_DIR")
  fi
  if [[ $? -ne 0 ]]; then
    rm -rf "$TMP_DIR"
    fail "the model conversion failed. Scroll up for the error. The download is kept, so running this again skips the download."
  fi
  if [[ ! -f "$TMP_DIR/config.json" || ! -f "$TMP_DIR/weights.safetensors" ]]; then
    rm -rf "$TMP_DIR"
    fail "the model conversion did not produce the expected files. Run this again."
  fi
  rm -rf "$ROMAN_DIR"
  mv "$TMP_DIR" "$ROMAN_DIR" || fail "could not move the converted model into $ROMAN_DIR"
  rm -rf "$DL_DIR"
  say "The speech model is installed: $ROMAN_DIR"
fi

# 8. Chrome steps
step "Installed. Three steps left in Chrome"
say "1. Open chrome://extensions"
say "2. Turn on Developer mode (top right)."
say "3. Press \"Load unpacked\" and choose this folder:"
say "   $REPO/extension"
say ""
say "Then click the extension icon on a lecture tab and press Record."
say ""
say "Optional: for a second hearing of every lesson (slower, needs 3 GB more), run:"
say "   zsh \"$REPO/install/get-second-model.command\""
say "Optional: notes written by an AI are an add-on, described in AI-SETUP-GUIDE.md."
say ""
say "New to this? Give the file AI-SETUP-GUIDE.md to any AI assistant and it will walk you through the rest."
