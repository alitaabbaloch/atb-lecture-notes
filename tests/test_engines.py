import json
import logging
import sys
import types

import pytest

import config
import engines
import transcriber
from conftest import wait_job


def words(texts, start=0.0):
    out, t = [], start
    for w in texts:
        out.append({"word": " " + w, "start": t, "end": t + 0.4})
        t += 0.5
    return out


SENTENCE = "one two three four five six."


@pytest.fixture
def stand_in(monkeypatch):
    """A stand-in `mlx_whisper` module: records the keyword arguments, returns one 30 s segment of two sentences."""
    module = types.ModuleType("mlx_whisper")
    module.calls = []
    module.segments = None

    def transcribe(samples, **kwargs):
        module.calls.append(kwargs)
        if module.segments is not None:
            return {"segments": module.segments}
        ws = words(SENTENCE.split()) + words("seven eight nine ten eleven twelve.".split(), 3.0)
        text = " " + SENTENCE + " seven eight nine ten eleven twelve."
        return {"text": text, "language": "en", "segments": [{"id": 0, "start": 0.0, "end": 30.0, "text": text,
                                                              "words": ws}]}

    module.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    return module


def test_roman_forces_english_and_word_times_and_passes_repair_options(stand_in):
    engine = engines.RomanEngine("/models/roman")
    engine.transcribe_array([0.0] * 16000, "ur", word_timestamps=False, no_speech_threshold=None,
                            temperature=(0.0, 0.2, 0.4))
    call = stand_in.calls[0]
    assert call["language"] == "en" and call["word_timestamps"] is True
    assert call["no_speech_threshold"] is None and call["temperature"] == (0.0, 0.2, 0.4)
    assert call["path_or_hf_repo"] == "/models/roman"
    with pytest.raises(ValueError):
        engine.transcribe_array([0.0], "ur", bogus=1)


def test_roman_result_is_sentence_lines_in_the_lesson_language(stand_in):
    result = engines.RomanEngine("/m").transcribe_array([0.0] * 16000, "ur")
    assert len(result["segments"]) > 1
    assert all("words" not in line for line in result["segments"])
    assert result["language"] == "ur"
    assert result["text"] == " ".join(line["text"] for line in result["segments"])
    assert engines.RomanEngine("/m").transcribe_array([0.0], None)["language"] == ""


def test_roman_nan_segment_gives_no_line(stand_in):
    stand_in.segments = [{"id": 0, "start": 0.0, "end": 30.0, "text": " nan", "words": []}]
    result = engines.RomanEngine("/m").transcribe_array([0.0] * 16000, "ur")
    assert result["segments"] == [] and result["text"] == ""


def test_roman_whole_file_method_is_not_used():
    with pytest.raises(NotImplementedError):
        engines.RomanEngine("/m").transcribe("x.webm", "ur")


def test_roman_installed(tmp_path):
    assert not engines.RomanEngine.installed(tmp_path / "missing")
    assert not engines.RomanEngine.installed(tmp_path)
    (tmp_path / "config.json").write_text("{}")
    assert not engines.RomanEngine.installed(tmp_path)
    (tmp_path / "weights.safetensors").write_bytes(b"x")
    assert engines.RomanEngine.installed(tmp_path)


def settings(env, monkeypatch, engine="mlx", **extra):
    monkeypatch.setenv("ATB_ENGINE", engine)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    return config.load()


def install_roman(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text("{}")
    (folder / "weights.safetensors").write_bytes(b"x")


def test_fake_setting_gives_one_shared_engine(env, monkeypatch):
    roles = engines.Engines(settings(env, monkeypatch, "fake"))
    fake = roles.primary("ur")
    assert isinstance(fake, engines.FakeEngine)
    assert roles.primary("en") is fake and roles.second() is fake


def test_roman_for_urdu_when_installed_and_wanted(env, monkeypatch, tmp_path):
    install_roman(tmp_path / "roman")
    cfg = settings(env, monkeypatch, ATB_ROMAN_MODEL=str(tmp_path / "roman"), ATB_TRANSCRIPT="roman")
    roles = engines.Engines(cfg)
    roman = roles.primary("ur")
    assert isinstance(roman, engines.RomanEngine) and roman.model == str(tmp_path / "roman")
    for language in ("en", None, ""):
        other = roles.primary(language)
        assert type(other) is engines.MlxEngine
    assert roles.primary("UR-PK") is roman


def test_urdu_transcript_setting_uses_whisper(env, monkeypatch, tmp_path):
    install_roman(tmp_path / "roman")
    cfg = settings(env, monkeypatch, ATB_ROMAN_MODEL=str(tmp_path / "roman"), ATB_TRANSCRIPT="urdu")
    assert type(engines.Engines(cfg).primary("ur")) is engines.MlxEngine


def test_missing_roman_model_falls_back_with_one_warning(env, monkeypatch, tmp_path, caplog):
    cfg = settings(env, monkeypatch, ATB_ROMAN_MODEL=str(tmp_path / "nope"))
    roles = engines.Engines(cfg)
    with caplog.at_level(logging.WARNING, logger="atb"):
        assert type(roles.primary("ur")) is engines.MlxEngine
        assert type(roles.primary("ur")) is engines.MlxEngine
    warnings = [r for r in caplog.records if "Roman model was not found" in r.getMessage()]
    assert len(warnings) == 1 and str(tmp_path / "nope") in warnings[0].getMessage()


def test_second_needs_the_cached_whisper_model(env, monkeypatch, tmp_path):
    cache = tmp_path / "hf"
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    roles = engines.Engines(settings(env, monkeypatch))
    with pytest.raises(RuntimeError, match="the second hearing needs the Whisper large-v3 model. "
                                          "Run install/get-second-model.command once."):
        roles.second()
    snap = cache / "models--mlx-community--whisper-large-v3-mlx" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "weights.safetensors").write_bytes(b"x")
    assert engines.whisper_cached()
    second = roles.second()
    assert type(second) is engines.MlxEngine and second.model == engines.WHISPER_REPO


def test_same_role_gives_the_same_object(env, monkeypatch, tmp_path):
    install_roman(tmp_path / "roman")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    snap = tmp_path / "hf" / "models--mlx-community--whisper-large-v3-mlx" / "snapshots" / "a"
    snap.mkdir(parents=True)
    (snap / "weights.npz").write_bytes(b"x")
    roles = engines.Engines(settings(env, monkeypatch, ATB_ROMAN_MODEL=str(tmp_path / "roman")))
    assert roles.primary("ur") is roles.primary("ur")
    assert roles.primary("en") is roles.primary("en")
    assert roles.second() is roles.second()


def test_config_defaults_and_overrides(env, monkeypatch, tmp_path):
    cfg = config.load()
    assert cfg.transcript == "roman" and cfg.second_hearing is False
    assert cfg.roman_model == env["app"] / "models" / "roman-urdu"
    monkeypatch.setenv("ATB_TRANSCRIPT", "urdu")
    monkeypatch.setenv("ATB_SECOND_HEARING", "1")
    monkeypatch.setenv("ATB_ROMAN_MODEL", str(tmp_path / "m"))
    cfg = config.load()
    assert (cfg.transcript, cfg.second_hearing, cfg.roman_model) == ("urdu", True, tmp_path / "m")
    monkeypatch.setenv("ATB_SECOND_HEARING", "0")
    monkeypatch.setenv("ATB_TRANSCRIPT", "klingon")
    cfg = config.load()
    assert cfg.transcript == "roman" and cfg.second_hearing is False


def test_config_file_values(env):
    (env["app"]).mkdir(parents=True, exist_ok=True)
    (env["app"] / "config.json").write_text(json.dumps({"transcript": "urdu", "second_hearing": True,
                                                        "roman_model": "/x/y"}))
    cfg = config.load()
    assert cfg.transcript == "urdu" and cfg.second_hearing is True and str(cfg.roman_model) == "/x/y"


def test_settings_hash_covers_the_engine_signature():
    class A(engines.FakeEngine):
        signature = "a-1"

    class B(engines.FakeEngine):
        signature = "a-2"

    opts = transcriber.Options(language="ur")
    assert (transcriber._settings_hash(A(), opts, 1.0, 10, (0, 5))
            != transcriber._settings_hash(B(), opts, 1.0, 10, (0, 5)))


class RomanFake(engines.FakeEngine):
    script = "roman"


def run_lesson(env, monkeypatch, webm20, engine_class):
    from fastapi.testclient import TestClient
    import server
    app = server.create_app()
    app.state.pipe.engines = engines.Single(engine_class())
    with TestClient(app, base_url=f"http://127.0.0.1:{env['port']}") as c:
        r = c.post("/transcribe", files={"audio": ("l.webm", webm20.read_bytes(), "audio/webm")},
                   data={"course": "Demo", "lesson": "Lesson", "speed": "1"})
        job = wait_job(c, r.json()["job_id"])
        return job, app.state.pipe


def test_script_key_reaches_front_matter_and_json(env, monkeypatch, webm20):
    job, _ = run_lesson(env, monkeypatch, webm20, RomanFake)
    md = env["data"] / job["output"]
    assert "script: roman" in md.read_text(encoding="utf-8").split("\n---", 1)[0]
    assert json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))["script"] == "roman"


def test_no_script_key_with_the_plain_fake_engine(env, monkeypatch, webm20):
    job, _ = run_lesson(env, monkeypatch, webm20, engines.FakeEngine)
    md = env["data"] / job["output"]
    assert "script:" not in md.read_text(encoding="utf-8").split("\n---", 1)[0]
    assert "script" not in json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))


def test_speed_is_stored_under_the_engine_that_ran(env, monkeypatch, webm20):
    job, pipe = run_lesson(env, monkeypatch, webm20, RomanFake)
    assert float(pipe.store.get_setting("speed_factor:fake:fake", "0")) > 0
    assert pipe.store.get_setting("speed_factor:fake:fake:n", "0") == "1"


def test_default_speed_per_engine_name(client):
    pipe = client.app.state.pipe

    class Named:
        model = "m"

    for name, expected in (("mlx-roman", 12.0), ("mlx", 2.5), ("fake", 3.0), ("other", 3.0)):
        engine = Named()
        engine.name = name
        assert pipe.speed_factor(engine) == expected


def test_health_has_the_new_and_the_old_keys(client):
    body = client.get("/health").json()
    for key in ("ok", "app", "version", "api", "nonce", "engine", "model", "language", "data_dir", "notes_dir",
                "busy", "queue", "transcript", "second_hearing", "script"):
        assert key in body, key
    assert body["transcript"] == "roman" and body["second_hearing"] is False and body["script"] is None
    assert body["engine"] == "fake"
