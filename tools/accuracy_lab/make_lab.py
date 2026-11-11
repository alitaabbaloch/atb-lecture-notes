#!/usr/bin/env python3
"""Build the blind listening test from your own recordings.

Usage: uv run python tools/accuracy_lab/make_lab.py --spec spec.json --out <folder> [--engine fake|mlx] [--seed N]

The spec lists recordings and the speed they were played at:
  {"clip_seconds": 25, "language": "ur",
   "lessons": [{"audio": "/abs/path/a.webm", "speed": 2.0, "clips": 4, "group": "fast"},
               {"audio": "/abs/path/b.webm", "speed": 1.0, "clips": 2, "group": "normal"}]}

The output folder holds private audio. It must be outside this repo, or under tools/accuracy_lab/out/.
"""
import argparse
import json
import random
import shutil
import string
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "server"))

import numpy as np  # noqa: E402

import audio  # noqa: E402
import engines  # noqa: E402
import transcriber  # noqa: E402
import vad  # noqa: E402

SR = 16000
FAST_FROM = 1.2          # same threshold the pipeline uses to slow audio back
SNAP_SECONDS = 3.0       # start a clip at a pause when one is this close
ASSETS = ("index.html", "lab.js", "lab.css")


def check_out_dir(out: Path) -> Path:
    """Refuse a folder inside the repo unless it is under the git-ignored tools/accuracy_lab/out/."""
    out = Path(out).expanduser().resolve()
    allowed = (ROOT / "tools" / "accuracy_lab" / "out").resolve()
    if out.is_relative_to(ROOT.resolve()) and not out.is_relative_to(allowed):
        raise SystemExit(f"Refusing to write private audio inside the repo: {out}\n"
                         f"Use a folder outside it, or one under {allowed}")
    return out


def pick_windows(spans, blocks, clip_seconds: float, count: int, rng: random.Random) -> list[tuple[float, float]]:
    """Choose up to `count` non-overlapping windows of recording time, each inside one block.

    The usable start positions are laid end to end and cut into `count` equal parts; one window is taken from
    each part at a position chosen by `rng`. A start near a pause is moved onto the pause."""
    usable = [(a, b - clip_seconds) for a, b in blocks if b - a >= clip_seconds - 1e-9]
    if not usable or count <= 0:
        return []
    count = min(count, int(sum(b - a for a, b in blocks if b - a >= clip_seconds) // clip_seconds))
    if count <= 0:
        return []
    total = sum(hi - lo for lo, hi in usable)
    starts = sorted(float(s) for s, _ in spans)
    picks = []  # (start, lo, hi)
    for i in range(count):
        x = (i + rng.random()) * total / count
        for lo, hi in usable:
            if x <= hi - lo or (lo, hi) == usable[-1]:
                break
            x -= hi - lo
        start = min(hi, lo + max(0.0, x))
        near = [c for c in starts if lo <= c <= hi and abs(c - start) <= SNAP_SECONDS]
        if near:
            start = min(near, key=lambda c: abs(c - start))
        picks.append((start, lo, hi))
    picks.sort()
    windows: list[tuple[float, float]] = []
    for start, lo, hi in picks:
        if windows and start < windows[-1][1]:
            start = windows[-1][1]  # shift forward behind the previous window
        if start > hi + 1e-9:
            continue
        windows.append((start, start + clip_seconds))
    return windows


def _text(segments) -> str:
    return " ".join(" ".join(str(s.get("text") or "") for s in segments).split())


def clip_candidates(clip, speed: float, engine, language: str) -> dict[str, str]:
    """The texts of every method for one clip of the recording (raw, at recorded speed)."""
    out = {"today": _text(engine.transcribe_array(clip, language or None).get("segments", []))}
    new = transcriber.Options(language=language or None, speed=speed, tempo_restore=False, repair=True)
    out["new"] = _text(transcriber.transcribe_samples(clip, engine, new)["segments"])
    if speed >= FAST_FROM:
        slow = transcriber.Options(language=language or None, speed=speed, tempo_restore=True, repair=True)
        out["new_slow"] = _text(transcriber.transcribe_samples(clip, engine, slow)["segments"])
    return out


def make_cards(candidates: dict[str, str], rng: random.Random):
    """Merge identical texts, shuffle, give opaque letters. Returns (cards, key) where key = {letter: [names]}."""
    merged: dict[str, list[str]] = {}
    for name, text in candidates.items():
        merged.setdefault(text, []).append(name)
    items = list(merged.items())
    rng.shuffle(items)
    letters = rng.sample(string.ascii_uppercase, len(items))
    return ([{"key": k, "text": t} for k, (t, _) in zip(letters, items)],
            {k: names for k, (_, names) in zip(letters, items)})


def listening_audio(clip: np.ndarray, speed: float) -> np.ndarray:
    return audio.change_tempo(clip, 1.0 / speed) if speed >= FAST_FROM else clip


def write_wav(path: Path, samples: np.ndarray) -> float:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return round(len(pcm) / SR, 2)


def build_lab(spec: dict, out: Path, engine, seed: int = 1) -> dict:
    out = check_out_dir(out)
    if (out / "key.json").exists():
        raise SystemExit(f"{out} already holds a lab. Use a new folder so earlier answers are not mixed in.")
    clip_seconds = float(spec.get("clip_seconds", 25))
    language = spec.get("language", "ur")
    lessons = spec.get("lessons") or []
    if not lessons or clip_seconds <= 0:
        raise SystemExit("The spec needs clip_seconds and at least one lesson.")
    (out / "clips").mkdir(parents=True, exist_ok=True)
    built = []  # (lesson index, group, window, file, key, cards, speed)
    skipped = 0
    for li, lesson in enumerate(lessons):
        path, speed = Path(lesson["audio"]), float(lesson.get("speed", 1.0))
        if not path.is_absolute() or not path.exists():
            raise SystemExit(f"Lesson {li + 1}: audio must be an absolute path to an existing file: {path}")
        group = lesson.get("group") or ("fast" if speed >= FAST_FROM else "normal")
        samples = audio.decode_16k_mono(path)
        total = len(samples) / SR
        spans = vad.speech_spans(samples, SR)
        blocks = vad.build_blocks(spans, total)
        rng = random.Random(f"{seed}:{li}")
        windows = pick_windows(spans, blocks, clip_seconds, int(lesson.get("clips", 1)), rng)
        print(f"lesson {li + 1}: {len(windows)} of {lesson.get('clips', 1)} clips picked", file=sys.stderr)
        for start, end in windows:
            clip = samples[int(start * SR): int(end * SR)]
            cards, key = make_cards(clip_candidates(clip, speed, engine, language), rng)
            if len(cards) < 2:
                skipped += 1  # every method wrote the same text: nothing to judge
                continue
            built.append({"lesson": li, "group": group, "start": round(start, 2), "end": round(end, 2),
                          "speed": speed, "audio": listening_audio(clip, speed), "cards": cards, "key": key,
                          "file": path.name})
    random.Random(f"{seed}:order").shuffle(built)  # mix lessons so the listener does not learn the pattern
    lab_clips, key_clips = [], {}
    for n, c in enumerate(built, 1):
        cid = f"clip-{n:02d}"
        seconds = write_wav(out / "clips" / f"{cid}.wav", c["audio"])
        lab_clips.append({"id": cid, "group": c["group"], "audio": f"clips/{cid}.wav", "seconds": seconds,
                          "candidates": c["cards"]})
        key_clips[cid] = {"keys": c["key"], "group": c["group"], "lesson": c["lesson"], "lesson_file": c["file"],
                          "start": c["start"], "end": c["end"], "speed": c["speed"]}
    lab_id = f"{random.Random(seed).getrandbits(48):012x}"
    (out / "lab.json").write_text(json.dumps({"id": lab_id, "created": int(time.time()), "clips": lab_clips},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps({"seed": seed, "clips": key_clips}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    for name in ASSETS:
        shutil.copy2(HERE / name, out / name)
    print(f"{len(built)} clips written to {out} ({skipped} skipped because every method agreed)", file=sys.stderr)
    return {"clips": len(built), "skipped": skipped, "out": str(out)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--engine", choices=("fake", "mlx"), default=None)
    p.add_argument("--seed", type=int, default=1)
    a = p.parse_args(argv)
    out = check_out_dir(Path(a.out))
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    build_lab(spec, out, engines.get_engine(a.engine or ""), a.seed)
    print(f"next: uv run python tools/accuracy_lab/run_lab.py --lab {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
