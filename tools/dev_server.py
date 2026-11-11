#!/usr/bin/env python3
"""Start the server for development on port 8766 with throwaway folders.

Usage: .venv/bin/python tools/dev_server.py [fake|mlx] [name]     (default: fake)
Same job as dev-server.sh, for launchers that cannot run a shell script.
The optional name gives this run its own throwaway folders (atb-lecture-notes-dev-<name>).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.path.abspath(__file__)).parent.parent
NAME = sys.argv[2] if len(sys.argv) > 2 else ""
BASE = Path(tempfile.gettempdir()) / ("atb-lecture-notes-dev" + (f"-{NAME}" if NAME.isalnum() else ""))

(BASE / "app").mkdir(parents=True, exist_ok=True)
(BASE / "data").mkdir(parents=True, exist_ok=True)
os.environ.update(
    ATB_APP_DIR=str(BASE / "app"),
    ATB_DATA_DIR=str(BASE / "data"),
    ATB_PORT="8766",
    ATB_DEV="1",
    ATB_ENGINE=sys.argv[1] if len(sys.argv) > 1 else "fake",
)
os.environ.setdefault("ATB_NOTES", "fake")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "server"))

import uvicorn  # noqa: E402

print(f"dev server on http://127.0.0.1:8766/dev/  (engine {os.environ['ATB_ENGINE']}, data in {BASE})", flush=True)
uvicorn.run("server:app", host="127.0.0.1", port=8766, app_dir=str(ROOT / "server"))
