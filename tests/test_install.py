"""Tests for install/install-mac.command and install/get-second-model.command.

No network and no real model: downloads use file:// folders and conversion uses a fake script.
Every run uses temporary ATB_APP_DIR and ATB_NM_ROOT folders.
"""
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("zsh") is None or platform.system() != "Darwin" or platform.machine() != "arm64",
    reason="the installer needs zsh on an Apple Silicon Mac",
)

INSTALL = Path(__file__).resolve().parent.parent / "install"
INSTALLER = INSTALL / "install-mac.command"
SECOND = INSTALL / "get-second-model.command"
SETTINGS = {
    "data_dir": None,
    "port": 9999,
    "language": "ur",
    "second_hearing": False,
    "custom_key": "keep me",
}


@pytest.fixture
def env(tmp_path):
    app = tmp_path / "app"
    nm = tmp_path / "nm"
    (nm / "Google" / "Chrome").mkdir(parents=True)
    e = dict(os.environ)
    e.update(ATB_APP_DIR=str(app), ATB_NM_ROOT=str(nm), ATB_SKIP_SYNC="1")
    for k in ("ATB_SKIP_MODEL", "ATB_ROMAN_URL_BASE", "ATB_CONVERT_CMD"):
        e.pop(k, None)
    e["_app"], e["_tmp"] = str(app), str(tmp_path)
    return e


def run(script, env):
    clean = {k: v for k, v in env.items() if not k.startswith("_")}
    return subprocess.run(["zsh", str(script)], env=clean, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


def source_folder(tmp):
    src = Path(tmp) / "source"
    src.mkdir()
    (src / "config.json").write_text('{"x": 1}')
    (src / "model.safetensors").write_bytes(b"fake weights")
    return src


def fake_convert(tmp, ok=True):
    marker = Path(tmp) / "convert-called"
    script = Path(tmp) / "convert.sh"
    if ok:
        body = f'echo run >> "{marker}"\nmkdir -p "$2"\ncp "$1/config.json" "$2/config.json"\necho w > "$2/weights.safetensors"\n'
    else:
        body = f'echo run >> "{marker}"\nexit 1\n'
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return script, marker


def with_model_env(env, base, convert):
    env["ATB_ROMAN_URL_BASE"] = base
    env["ATB_CONVERT_CMD"] = str(convert)


def test_install_skip_model(env):
    env["ATB_SKIP_MODEL"] = "1"
    r = run(INSTALLER, env)
    assert r.returncode == 0, r.stdout + r.stderr
    app = Path(env["_app"])
    cfg = json.loads((app / "config.json").read_text())
    assert cfg["transcript"] == "roman"
    assert cfg["second_hearing"] is False
    assert cfg["auto_notes"] is False
    wrapper = app / "native-host.sh"
    assert wrapper.exists() and os.access(wrapper, os.X_OK)
    assert (Path(env["ATB_NM_ROOT"]) / "Google/Chrome/NativeMessagingHosts/com.atb.lecture_notes.json").exists()
    assert not (app / "models").exists()
    assert "get-second-model.command" in r.stdout


def test_install_model_from_local_files(env):
    src = source_folder(env["_tmp"])
    convert, _ = fake_convert(env["_tmp"])
    with_model_env(env, f"file://{src}", convert)
    r = run(INSTALLER, env)
    assert r.returncode == 0, r.stdout + r.stderr
    models = Path(env["_app"]) / "models"
    assert (models / "roman-urdu/config.json").exists()
    assert (models / "roman-urdu/weights.safetensors").exists()
    assert not (models / "roman-urdu.download").exists()
    assert not (models / "roman-urdu.tmp").exists()


def test_install_second_run_skips_model(env):
    src = source_folder(env["_tmp"])
    convert, marker = fake_convert(env["_tmp"])
    with_model_env(env, f"file://{src}", convert)
    assert run(INSTALLER, env).returncode == 0
    assert marker.read_text().count("run") == 1
    r = run(INSTALLER, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already installed" in r.stdout
    assert marker.read_text().count("run") == 1


def test_install_download_failure(env):
    convert, marker = fake_convert(env["_tmp"])
    with_model_env(env, "file:///nonexistent-folder", convert)
    r = run(INSTALLER, env)
    assert r.returncode != 0
    assert "download failed" in r.stdout
    assert not (Path(env["_app"]) / "models/roman-urdu").exists()
    assert not marker.exists()


def test_install_convert_failure(env):
    src = source_folder(env["_tmp"])
    convert, _ = fake_convert(env["_tmp"], ok=False)
    with_model_env(env, f"file://{src}", convert)
    r = run(INSTALLER, env)
    assert r.returncode != 0
    models = Path(env["_app"]) / "models"
    assert not (models / "roman-urdu").exists()
    assert not (models / "roman-urdu.tmp").exists()


def test_install_keeps_existing_config(env):
    env["ATB_SKIP_MODEL"] = "1"
    app = Path(env["_app"])
    app.mkdir()
    original = b'{"port":1234,   "weird":  true}\n'
    (app / "config.json").write_bytes(original)
    assert run(INSTALLER, env).returncode == 0
    assert (app / "config.json").read_bytes() == original


def test_second_model_switches_on(env):
    env["ATB_SKIP_MODEL"] = "1"
    app = Path(env["_app"])
    app.mkdir()
    (app / "config.json").write_text(json.dumps(SETTINGS))
    r = run(SECOND, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((app / "config.json").read_text()) == {**SETTINGS, "second_hearing": True}
    assert run(SECOND, env).returncode == 0  # safe to run twice


def test_second_model_without_config(env):
    env["ATB_SKIP_MODEL"] = "1"
    r = run(SECOND, env)
    assert r.returncode != 0
    assert not (Path(env["_app"]) / "config.json").exists()


def test_second_model_bad_json_untouched(env):
    env["ATB_SKIP_MODEL"] = "1"
    app = Path(env["_app"])
    app.mkdir()
    (app / "config.json").write_text("{not json")
    assert run(SECOND, env).returncode != 0
    assert (app / "config.json").read_text() == "{not json"


@pytest.mark.parametrize("name", ["install-mac.command", "get-second-model.command", "uninstall-mac.command"])
def test_scripts_pass_syntax_check(name):
    r = subprocess.run(["zsh", "-n", str(INSTALL / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_failed_convert_keeps_download_and_markers(env):
    src = source_folder(env["_tmp"])
    convert, _ = fake_convert(env["_tmp"], ok=False)
    with_model_env(env, f"file://{src}", convert)
    assert run(INSTALLER, env).returncode != 0
    dl = Path(env["_app"]) / "models/roman-urdu.download"
    assert {p.name for p in dl.iterdir()} == {"config.json", "model.safetensors", "config.json.done", "model.safetensors.done"}


def test_rerun_does_not_download_finished_files(env):
    src = source_folder(env["_tmp"])
    bad, _ = fake_convert(env["_tmp"], ok=False)
    with_model_env(env, f"file://{src}", bad)
    assert run(INSTALLER, env).returncode != 0
    good = Path(env["_tmp"]) / "good-convert.sh"
    good.write_text('#!/bin/sh\nmkdir -p "$2"\ncp "$1/config.json" "$2/config.json"\necho w > "$2/weights.safetensors"\n')
    good.chmod(0o755)
    with_model_env(env, "file:///nonexistent-folder", good)
    r = run(INSTALLER, env)
    assert r.returncode == 0, r.stdout + r.stderr
    models = Path(env["_app"]) / "models"
    assert (models / "roman-urdu/weights.safetensors").exists()
    assert not (models / "roman-urdu.download").exists()


def test_partial_download_fetches_only_missing_file(env):
    base = Path(env["_tmp"]) / "only-weights"
    base.mkdir()
    (base / "model.safetensors").write_bytes(b"fake weights")
    dl = Path(env["_app"]) / "models/roman-urdu.download"
    dl.mkdir(parents=True)
    (dl / "config.json").write_text('{"x": 1}')
    (dl / "config.json.done").write_text("")
    convert, marker = fake_convert(env["_tmp"])
    with_model_env(env, f"file://{base}", convert)
    r = run(INSTALLER, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (Path(env["_app"]) / "models/roman-urdu/config.json").exists()
    assert marker.exists()
