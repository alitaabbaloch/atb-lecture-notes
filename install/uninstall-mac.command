#!/bin/zsh
# ATB Lecture Notes: Mac uninstaller.
# Test overrides: ATB_APP_DIR, ATB_NM_ROOT, ATB_PORT

set -u

APP_DIR="${ATB_APP_DIR:-$HOME/Library/Application Support/ATB Lecture Notes}"
NM_ROOT="${ATB_NM_ROOT:-$HOME/Library/Application Support}"
HOST_NAME="com.atb.lecture_notes"

say() { print -r -- "$*"; }
step() { print -r -- ""; print -r -- "==> $*"; }

say "ATB Lecture Notes uninstaller"

# 1. Stop the server
step "Asking the server to stop"
PORT="${ATB_PORT:-}"
if [[ -z "$PORT" && -f "$APP_DIR/config.json" ]]; then
  PORT="$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$APP_DIR/config.json" | grep -o '[0-9]*$' | head -n 1)"
fi
PORT="${PORT:-8765}"
HEALTH="$(curl -s --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)"
if [[ "$HEALTH" != *'"atb-lecture-notes"'* ]]; then
  say "The server is not running."
elif [[ ! -f "$APP_DIR/admin.token" ]]; then
  say "The server is running but its admin token is missing. Close it by hand."
else
  TOKEN="$(<"$APP_DIR/admin.token")"
  CODE="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' -X POST -H "X-Admin-Token: $TOKEN" "http://127.0.0.1:$PORT/admin/shutdown")"
  case "$CODE" in
    200) say "Server stopped." ;;
    409) say "The server is busy with a recording or a transcript. Close it later with start-server or by quitting the process." ;;
    *)   say "The server did not stop (HTTP $CODE)." ;;
  esac
fi

# 2. Browser manifests
step "Removing browser registrations"
removed=0
for sub in "Google/Chrome" "Google/Chrome Beta" "Chromium" "BraveSoftware/Brave-Browser" "Microsoft Edge"; do
  f="$NM_ROOT/$sub/NativeMessagingHosts/$HOST_NAME.json"
  if [[ -f "$f" ]]; then
    rm -f "$f" && say "Removed $f" && removed=1
  fi
done
[[ $removed == 1 ]] || say "No registrations found."

# 3. Wrapper
step "Removing the start helper"
if [[ -f "$APP_DIR/native-host.sh" ]]; then
  rm -f "$APP_DIR/native-host.sh" && say "Removed $APP_DIR/native-host.sh"
else
  say "Not found."
fi

# 4. App folder (optional)
step "App folder"
say "$APP_DIR holds your settings, the library database and logs."
read "?Delete it? [y/N] " answer
if [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]]; then
  rm -rf "$APP_DIR" && say "Deleted $APP_DIR"
else
  say "Kept."
fi

say ""
say "Your lessons (notes and recordings) were not touched."
say "If you downloaded the optional Whisper model for the second hearing, it is in the Hugging Face cache folder (${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}), about 3 GB. It was not deleted."
say "To remove the extension, open chrome://extensions and press Remove."
