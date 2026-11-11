"""ffmpeg helpers. The binary comes from imageio-ffmpeg so no system install is needed."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg") or "ffmpeg"


def remux_webm(src: Path, dst: Path) -> bool:
    """Copy the audio into a clean WebM (adds cues and duration). Returns False if ffmpeg fails."""
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        r = subprocess.run(
            [ffmpeg_exe(), "-nostdin", "-y", "-v", "error", "-i", str(src), "-c", "copy", "-f", "webm", str(tmp)],
            capture_output=True, timeout=900,
        )
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            return False
        os.replace(tmp, dst)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _hms(text: str) -> float:
    h, m, s = text.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def duration_seconds(path: Path) -> float | None:
    """Read the duration from ffmpeg's banner; if the file has none, decode it and take the last timestamp."""
    try:
        r = subprocess.run([ffmpeg_exe(), "-nostdin", "-i", str(path)], capture_output=True, timeout=60)
        m = re.search(r"Duration:\s*(\d+:\d+:\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "replace"))
        if m:
            return _hms(m.group(1))
        r = subprocess.run([ffmpeg_exe(), "-nostdin", "-i", str(path), "-f", "null", "-"],
                           capture_output=True, timeout=900)
        times = re.findall(r"time=(\d+:\d+:\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "replace"))
        return _hms(times[-1]) if times else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def atempo_filter(tempo: float) -> str:
    """ffmpeg's atempo accepts 0.5 to 2.0 per filter, so chain filters for values outside that range."""
    parts = []
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        parts.append("atempo=2.0")
        tempo /= 2.0
    parts.append(f"atempo={tempo:.6f}")
    return ",".join(parts)


def decode_16k_mono(path: Path, tempo: float = 1.0) -> np.ndarray:
    """Decode any audio file to float32 mono 16 kHz, the input Whisper expects. tempo < 1 slows it down."""
    cmd = [ffmpeg_exe(), "-nostdin", "-v", "error", "-i", str(path)]
    if tempo != 1.0:
        cmd += ["-af", atempo_filter(tempo)]
    cmd += ["-f", "s16le", "-ac", "1", "-ar", "16000", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg could not decode the audio: " + r.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def change_tempo(samples: np.ndarray, tempo: float) -> np.ndarray:
    """Time-stretch 16 kHz mono samples without changing pitch (used when a caller hands us raw samples)."""
    if tempo == 1.0 or not len(samples):
        return samples
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    cmd = [ffmpeg_exe(), "-nostdin", "-v", "error", "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "-",
           "-af", atempo_filter(tempo), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"]
    r = subprocess.run(cmd, input=pcm, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg could not change the tempo: " + r.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
