"""Paths, settings and the allowed extension id. Everything is read at call time so tests can set env first."""
import base64
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "ATB Lecture Notes"
APP_ID = "atb-lecture-notes"
VERSION = "1.0.0"
API = 2
FALLBACK_EXTENSION_ID = "flkffpbhlmeolggahhancjnpcjnoggaa"
REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "data_dir": None, "port": 8765, "language": "ur", "engine": "auto", "model": "auto",
    "auto_notes": False, "tempo_restore": True, "repair": True, "stale_session_seconds": 300, "startup_grace_seconds": 43200, "allowed_extension_ids": [],
    "glossary": True, "glossary_prompt": False, "starter_glossary": "seo-starter",
    "notes": {"provider": "auto"},
    "transcript": "roman", "second_hearing": False, "roman_model": None,
}


def app_dir() -> Path:
    override = os.environ.get("ATB_APP_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    return Path.home() / ".config" / APP_ID


def extension_id_from_key(key_b64: str) -> str:
    """Chrome derives the id from the public key: sha256, first 16 bytes, hex digits mapped to a-p."""
    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def manifest_extension_id() -> str:
    try:
        manifest = json.loads((REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        return extension_id_from_key(manifest["key"])
    except (OSError, ValueError, KeyError, TypeError):
        return FALLBACK_EXTENSION_ID


def load_secrets(directory: Path) -> None:
    """KEY=value lines from secrets.env go into the environment without overriding existing variables."""
    try:
        text = (directory / "secrets.env").read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class Settings:
    app_dir: Path
    data_dir: Path
    port: int
    language: str
    engine: str
    model: str  # "" means choose automatically
    auto_notes: bool
    stale_session_seconds: float
    startup_grace_seconds: float
    allowed_extension_ids: set = field(default_factory=set)
    dev: bool = False
    tempo_restore: bool = True
    repair: bool = True
    glossary: bool = True
    glossary_prompt: bool = False
    starter_glossary: str = "seo-starter"
    notes_provider: str = "auto"
    transcript: str = "roman"  # "roman" or "urdu"
    second_hearing: bool = False
    roman_model: Path = Path("models/roman-urdu")

    @property
    def db_path(self) -> Path:
        return self.app_dir / "library.db"

    @property
    def logs_dir(self) -> Path:
        return self.app_dir / "logs"


def load() -> Settings:
    directory = app_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "logs").mkdir(exist_ok=True)
    load_secrets(directory)
    path = directory / "config.json"
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(path.read_text(encoding="utf-8")))
    except OSError:
        path.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
    except ValueError:
        pass  # a broken file must not stop the server; defaults apply
    env = os.environ
    data_dir = env.get("ATB_DATA_DIR") or data["data_dir"] or "~/Documents/ATB Lecture Notes"
    language = env["WHISPER_LANGUAGE"] if "WHISPER_LANGUAGE" in env else data["language"]
    model = env.get("WHISPER_MODEL") or ("" if data["model"] in (None, "auto") else data["model"])
    auto_notes = (env["AUTO_NOTES"] != "0") if "AUTO_NOTES" in env else bool(data["auto_notes"])
    flag = lambda name, key: (env[name] != "0") if name in env else bool(data[key])  # noqa: E731
    transcript = str(env.get("ATB_TRANSCRIPT") or data["transcript"] or "roman").strip().lower()
    roman_model = env.get("ATB_ROMAN_MODEL") or data["roman_model"] or directory / "models" / "roman-urdu"
    ids = {manifest_extension_id(), *map(str, data["allowed_extension_ids"] or [])}
    return Settings(
        app_dir=directory,
        data_dir=Path(data_dir).expanduser(),
        port=int(env.get("ATB_PORT") or data["port"]),
        language=language or "",
        engine=env.get("ATB_ENGINE") or data["engine"] or "auto",
        model=model,
        auto_notes=auto_notes,
        stale_session_seconds=float(data["stale_session_seconds"]),
        startup_grace_seconds=float(data["startup_grace_seconds"]),
        allowed_extension_ids=ids,
        dev=env.get("ATB_DEV") == "1",
        tempo_restore=flag("ATB_TEMPO_RESTORE", "tempo_restore"),
        repair=flag("ATB_REPAIR", "repair"),
        glossary=flag("ATB_GLOSSARY", "glossary"),
        glossary_prompt=bool(data["glossary_prompt"]),
        starter_glossary=str(data["starter_glossary"] or ""),
        transcript=transcript if transcript in ("roman", "urdu") else "roman",
        second_hearing=flag("ATB_SECOND_HEARING", "second_hearing"),
        roman_model=Path(roman_model).expanduser(),
        notes_provider=str((data["notes"] or {}).get("provider") or "auto") if isinstance(data["notes"], dict) else "auto",
    )
