"""Speech-to-text engines. Heavy imports happen on first use so the server starts fast."""
import logging
import math
import os
import threading
import time
from pathlib import Path

import audio
import sentences

log = logging.getLogger("atb")

WHISPER_REPO = "mlx-community/whisper-large-v3-mlx"
DEFAULT_SPEED = {"mlx-roman": 12.0, "mlx": 2.5, "fake": 3.0}  # audio seconds per second, before anything is measured


def hf_cache() -> Path:
    """The Hugging Face hub cache folder, honouring HF_HUB_CACHE and HF_HOME like the library does."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _has_weights(snap: Path) -> bool:
    if (snap / "model.safetensors").exists() and not (snap / "weights.safetensors").exists():
        (snap / "weights.safetensors").symlink_to("model.safetensors")  # name mlx_whisper expects
    return (snap / "weights.npz").exists() or (snap / "weights.safetensors").exists()


def whisper_cached(repo: str = WHISPER_REPO) -> bool:
    """True when the model's snapshot folder in the Hugging Face cache holds weights."""
    d = hf_cache() / ("models--" + repo.replace("/", "--")) / "snapshots"
    return any(_has_weights(snap) for snap in (d.iterdir() if d.exists() else []))


def default_model() -> str:
    """Use the best model that is already downloaded; fall back to the small fast one."""
    for name in (WHISPER_REPO, "mlx-community/whisper-large-v3-turbo", "mlx-community/whisper-large-v3-turbo-4bit"):
        if whisper_cached(name):
            return name
    return "mlx-community/whisper-large-v3-turbo-4bit"


DECODE_KEYS = {"initial_prompt", "no_speech_threshold", "temperature", "compression_ratio_threshold",
               "logprob_threshold", "word_timestamps", "hallucination_silence_threshold"}


def check_decode(decode: dict) -> dict:
    bad = set(decode) - DECODE_KEYS
    if bad:
        raise ValueError(f"unknown decode options: {sorted(bad)}")
    return decode


class MlxEngine:
    name = "mlx"
    script = None
    signature = ""

    def __init__(self, model: str = ""):
        self.model = model or default_model()

    def transcribe(self, audio_path, language, on_progress=None) -> dict:
        try:
            import mlx_whisper
        except ImportError as e:
            raise RuntimeError("mlx-whisper is not installed (it needs an Apple Silicon Mac)") from e
        if on_progress:
            on_progress(0.0)
        samples = audio.decode_16k_mono(Path(audio_path))
        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self.model,
            language=language or None,
            task="transcribe",
            condition_on_previous_text=False,
            verbose=False,
        )
        if on_progress:
            on_progress(1.0)
        return {"text": result.get("text", ""), "segments": result.get("segments", []),
                "language": result.get("language") or language or ""}


    def transcribe_array(self, samples, language, **decode) -> dict:
        """One Whisper call on a float32 16 kHz array. `decode` keys are passed through (see DECODE_KEYS)."""
        try:
            import mlx_whisper
        except ImportError as e:
            raise RuntimeError("mlx-whisper is not installed (it needs an Apple Silicon Mac)") from e
        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self.model,
            language=language or None,
            task="transcribe",
            condition_on_previous_text=False,
            verbose=False,
            **check_decode(decode),
        )
        return {"text": result.get("text", ""), "segments": result.get("segments", []),
                "language": result.get("language") or language or ""}


class RomanEngine(MlxEngine):
    """Whisper large-v3-turbo fine-tune that writes Roman Urdu. Decoded as "en", cut into sentence lines."""
    name = "mlx-roman"
    script = "roman"
    signature = "roman-3"  # bump whenever the cutting rules change
    label = "roman-urdu (Whisper-Hindi2Hinglish-Apex)"  # written into transcripts instead of the local folder path

    def __init__(self, model_path):
        self.model = str(model_path)

    def transcribe(self, audio_path, language, on_progress=None) -> dict:
        raise NotImplementedError("the Roman engine only works on arrays (transcribe_array)")

    def transcribe_array(self, samples, language, **decode) -> dict:
        try:
            import mlx_whisper
        except ImportError as e:
            raise RuntimeError("mlx-whisper is not installed (it needs an Apple Silicon Mac)") from e
        decode = {**check_decode(decode), "word_timestamps": True}
        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self.model,
            language="en",
            task="transcribe",
            condition_on_previous_text=False,
            verbose=False,
            **decode,
        )
        lines = sentences.cut(result.get("segments", []))
        return {"text": " ".join(line["text"] for line in lines), "segments": lines, "language": language or ""}

    @staticmethod
    def installed(model_path) -> bool:
        folder = Path(model_path)
        return (folder / "config.json").exists() and (
            (folder / "weights.safetensors").exists() or (folder / "weights.npz").exists())


class FakeEngine:
    """No model. Returns invented segments after a pause (ATB_FAKE_SECONDS, default 0.5)."""
    name = "fake"
    model = "fake"
    script = None
    signature = ""

    def transcribe(self, audio_path, language, on_progress=None) -> dict:
        total = float(os.environ.get("ATB_FAKE_SECONDS", "0.5"))
        steps = 5
        for i in range(steps):
            time.sleep(total / steps)
            if on_progress:
                on_progress((i + 1) / steps)
        segments = [
            {"start": 0.0, "end": 4.0, "text": " First invented line of the lecture."},
            {"start": 4.0, "end": 9.5, "text": " Second line with a key term."},
            {"start": 9.5, "end": 15.0, "text": " Third and last line."},
        ]
        return {"text": "".join(s["text"] for s in segments).strip(), "segments": segments,
                "language": language or "en"}


    def transcribe_array(self, samples, language, **decode) -> dict:
        """About one 4 s segment per 4 s of audio. Test hooks (times are in the time base of `samples`):
        ATB_FAKE_LOOP_AT="12.0,95.0" turns the segment covering each time into a loop, unless the call passes
        no_speech_threshold=None (a repair attempt); ATB_FAKE_CAPTION_AT="40.0" turns it into a music caption."""
        check_decode(decode)
        time.sleep(float(os.environ.get("ATB_FAKE_SECONDS", "0.5")))
        seconds = len(samples) / 16000.0
        loops = _times("ATB_FAKE_LOOP_AT")
        captions = _times("ATB_FAKE_CAPTION_AT")
        repair = "no_speech_threshold" in decode and decode["no_speech_threshold"] is None
        segments = []
        for i in range(math.ceil(seconds / 4.0 - 1e-9)):
            start, end = i * 4.0, min(seconds, i * 4.0 + 4.0)
            text = f" Line {i} covers topic {(i * 7) % 13} with key term {i * 3 % 11} and idea {i}."
            if os.environ.get("ATB_FAKE_URDU") == "1":  # test hook: an Urdu-script word in front of every line
                text = " \u06cc\u06c1" + text
            if any(start <= t < end for t in captions):
                text = " \u0645\u0648\u0633\u06cc\u0642\u06cc"
            elif not repair and any(start <= t < end for t in loops):
                text = " " + "word " * 30
            segments.append({"id": i, "start": start, "end": end, "text": text, "temperature": 0.0,
                             "avg_logprob": -0.2, "compression_ratio": 1.5, "no_speech_prob": 0.05})
        return {"text": "".join(s["text"] for s in segments).strip(), "segments": segments,
                "language": language or "en"}


def _times(name: str) -> list[float]:
    out = []
    for part in os.environ.get(name, "").split(","):
        try:
            out.append(float(part))
        except ValueError:
            pass
    return out


class Engines:
    """The speech engines of one server, by role. Each is created on first use."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._made: dict = {}
        self._warned = False
        self._lock = threading.Lock()

    def _get(self, key, make):
        with self._lock:
            if key not in self._made:
                self._made[key] = make()
            return self._made[key]

    def primary(self, language):
        cfg = self.cfg
        if cfg.engine == "fake":
            return self._get("fake", FakeEngine)
        if cfg.transcript == "roman" and (language or "").lower().startswith("ur"):
            if RomanEngine.installed(cfg.roman_model):
                return self._get(("roman", str(cfg.roman_model)), lambda: RomanEngine(cfg.roman_model))
            self._warn_missing()
        return self._get(("whisper", cfg.model), lambda: MlxEngine(cfg.model))

    def second(self):
        if self.cfg.engine == "fake":
            return self._get("fake", FakeEngine)
        if not whisper_cached():
            raise RuntimeError("the second hearing needs the Whisper large-v3 model. "
                               "Run install/get-second-model.command once.")
        return self._get(("second", WHISPER_REPO), lambda: MlxEngine(WHISPER_REPO))

    def _warn_missing(self):
        with self._lock:
            if self._warned:
                return
            self._warned = True
        log.warning("the Roman model was not found at %s; Whisper is used instead", self.cfg.roman_model)


class Single:
    """Wraps one engine object so it fills both roles (for callers that pass a bare engine)."""

    def __init__(self, engine):
        self.engine = engine

    def primary(self, language):
        return self.engine

    def second(self):
        return self.engine


def get_engine(name: str = "", model: str = ""):
    name = name or os.environ.get("ATB_ENGINE") or "auto"
    if name == "fake":
        return FakeEngine()
    return MlxEngine(model)
