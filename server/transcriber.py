"""The v2 transcription flow: remove non-speech, restore playback speed, transcribe in blocks, repair loops.

Inside the flow every time is "whisper time" (the time base of the, possibly slowed, audio). Only the final
result is converted to recording seconds: recording_time = whisper_time * tempo.
"""
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import audio
import guard
import vad

log = logging.getLogger("atb")

SR = 16000
REPAIR_DECODE = {"no_speech_threshold": None, "temperature": (0.0, 0.2, 0.4)}  # ONLY for repair attempts
REPAIR_LEAD = (2.0, 7.0)   # seconds before the window, one per attempt
REPAIR_TRAIL = 2.0
SPEECH_MARGIN_DB = 20.0   # a frame counts as speech-level when it is within this of the lesson's speech level
SPEECH_SHARE = 0.4        # a stretch is worth decoding again when this share of its frames is speech-level
MIN_GAIN_WORDS = 3        # a new decode must add at least this many words
FIRST_PASS_SHARE = 0.9  # progress: first pass 0 to 0.9, repair 0.9 to 1.0, 1.0 only at the very end
LONG_GAP = 3.0  # seconds of the recording: a longer silence is a break and is left out
KEEP_KEYS = ("start", "end", "text", "avg_logprob", "compression_ratio", "no_speech_prob", "temperature")


@dataclass
class Options:
    language: str | None = "ur"
    speed: float = 1.0
    tempo_restore: bool = True     # slow by 1/speed when speed >= 1.2
    repair: bool = True
    initial_prompt: str | None = None
    block_target: float = 300.0


def tempo_for(opts: Options) -> float:
    return 1.0 / opts.speed if opts.tempo_restore and opts.speed >= 1.2 else 1.0


def _trim(seg: dict) -> dict:
    out = {k: seg.get(k) for k in KEEP_KEYS}
    out["start"], out["end"] = float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)
    out["text"] = str(seg.get("text") or "")
    out["temperature"] = seg.get("temperature") or 0.0
    return out


def _stage(name: str, t0: float, extra: str = "") -> None:
    log.info("transcriber %s: %.1fs %s", name, time.time() - t0, extra)


def _settings_hash(engine, opts: Options, tempo: float, size: int, block) -> str:
    key = json.dumps([engine.name, engine.model, getattr(engine, "signature", ""), opts.language, opts.initial_prompt, opts.block_target, opts.speed,
                      opts.tempo_restore, round(tempo, 6), size, round(block[0], 3), round(block[1], 3)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def transcribe_lesson(audio_path, engine, opts: Options, work_dir=None, on_progress=None) -> dict:
    """Decode the file (slowed back to lesson speed when asked) and run the flow. Returns result v2."""
    t0 = time.time()
    tempo = tempo_for(opts)
    samples = audio.decode_16k_mono(Path(audio_path), tempo)
    _stage("decode", t0, f"tempo {tempo:.4f}, {len(samples) / SR:.1f}s of audio")
    size = Path(audio_path).stat().st_size
    return _run(samples, engine, opts, tempo, work_dir, on_progress, size)


def transcribe_samples(samples, engine, opts: Options, speed_already_applied: bool = False) -> dict:
    """The same flow on an array (16 kHz mono float32), without checkpoints.

    speed_already_applied=True means the samples were already slowed by 1/speed; otherwise they are the raw
    recording and are slowed here when the options ask for it."""
    samples = np_array(samples)
    tempo = tempo_for(opts)
    if not speed_already_applied and tempo != 1.0:
        samples = audio.change_tempo(samples, tempo)
    return _run(samples, engine, opts, tempo, None, None, 0)


def np_array(samples):
    import numpy as np
    return np.asarray(samples, dtype=np.float32)


def _run(samples, engine, opts: Options, tempo: float, work_dir, on_progress, size: int) -> dict:
    t0 = time.time()
    total = len(samples) / SR
    blocks = vad.build_blocks(vad.speech_spans(samples, SR), total, target=opts.block_target,
                              long_gap=LONG_GAP / tempo)
    _stage("vad", t0, f"{len(blocks)} blocks, {sum(b - a for a, b in blocks):.1f}s of speech")

    work = Path(work_dir) if work_dir else None
    if work:
        work.mkdir(parents=True, exist_ok=True)
    block_seconds = sum(b - a for a, b in blocks)
    done_seconds = 0.0
    if on_progress:
        on_progress(0.0)
    segments: list[dict] = []
    language = opts.language or ""
    t1 = time.time()
    reused = 0
    for n, (start, end) in enumerate(blocks):
        sig = _settings_hash(engine, opts, tempo, size, (start, end))
        cache = work / f"block-{n:03d}.json" if work else None
        data = None
        if cache and cache.exists():
            try:
                loaded = json.loads(cache.read_text(encoding="utf-8"))
                if loaded.get("hash") == sig:
                    data = loaded
                    reused += 1
            except (OSError, ValueError):
                data = None
        if data is None:
            piece = samples[int(start * SR): int(end * SR)]
            kwargs = {"initial_prompt": opts.initial_prompt} if opts.initial_prompt else {}
            out = engine.transcribe_array(piece, opts.language or None, **kwargs)
            data = {"hash": sig, "language": out.get("language") or "",
                    "segments": [_trim(s) for s in out.get("segments", [])]}
            if cache:
                _write_atomic(cache, data)
        language = language or data.get("language") or ""
        for seg in data["segments"]:
            seg = dict(seg)
            seg["start"] += start
            seg["end"] += start
            seg["block"] = n
            seg["repaired"] = False
            segments.append(seg)
        done_seconds += end - start
        if on_progress:
            on_progress(FIRST_PASS_SHARE * done_seconds / block_seconds if block_seconds else FIRST_PASS_SHARE)
    _stage("blocks", t1, f"{len(segments)} segments, {reused} blocks reused")

    t2 = time.time()
    segments, issues = guard.drop_non_speech(segments)
    windows = guard.find_bad_windows(segments)
    _stage("guard", t2, f"{len(windows)} bad windows")

    repaired_windows = 0
    if opts.repair and windows:
        t3 = time.time()
        targets = [(w, [segments[i] for i in w.segment_ids]) for w in windows]  # objects, so list changes are safe
        for n, (window, flagged) in enumerate(targets, 1):
            segments, new_issues, ok = _repair_window(segments, window, flagged, samples, engine, opts, total)
            issues.extend(new_issues)
            repaired_windows += 1 if ok else 0
            if on_progress:
                on_progress(FIRST_PASS_SHARE + 0.09 * n / len(targets))
        segments, more = guard.drop_non_speech(segments)  # a repair attempt can contain a caption too
        issues.extend(more)
        _stage("repair", t3, f"{repaired_windows} of {len(windows)} windows repaired")
    elif windows:  # repair is off: keep the good lines, mark the rest unclear
        drop = {id(segments[i]) for w in windows for i in w.segment_ids}
        for w in windows:
            issues.append(_unclear([segments[i] for i in w.segment_ids], w.reasons))
        segments = [s for s in segments if id(s) not in drop]

    if opts.repair:
        t4 = time.time()
        segments, more, filled = _fill_no_text(segments, blocks, issues, samples, engine, opts, total)
        issues.extend(more)
        repaired_windows += filled
        _stage("no_text", t4, f"{filled} stretches filled")

    result = _finish(segments, issues, blocks, total, tempo, opts, engine, language, repaired_windows)
    if on_progress:
        on_progress(1.0)
    _stage("done", t0, f"{len(result['segments'])} segments")
    return result


def _unclear(flagged, reasons) -> dict:
    return {"kind": "unclear", "start": flagged[0]["start"], "end": max(s["end"] for s in flagged),
            "detail": ",".join(reasons)}


def _words(segs) -> int:
    return sum(len(str(s["text"]).split()) for s in segs)


def _repair_window(segments, window, flagged, samples, engine, opts, total):
    """Try up to two fresh decodes. Returns (segments, new issues, repaired?). Never drops unflagged speech.

    `flagged` are the flagged segment objects of this window."""
    live = {id(s) for s in segments}
    flagged = [s for s in flagged if id(s) in live]
    if not flagged:  # an earlier repair already replaced them
        return segments, [], False
    flagged_ids = {id(s) for s in flagged}
    for lead in REPAIR_LEAD:
        a, b = max(0.0, window.start - lead), min(total, window.end + REPAIR_TRAIL)
        for _ in range(3):  # widen the slice to whole first-pass segments, so no half segment is lost
            over = [s for s in segments if s["start"] < b and s["end"] > a]
            na = min([a] + [s["start"] for s in over])
            nb = max([b] + [s["end"] for s in over])
            if (na, nb) == (a, b):
                break
            a, b = max(0.0, na), min(total, nb)
        if b - a < 0.5:
            continue
        piece = samples[int(a * SR): int(b * SR)]
        out = engine.transcribe_array(piece, opts.language or None, **REPAIR_DECODE,
                                      **({"initial_prompt": opts.initial_prompt} if opts.initial_prompt else {}))
        attempt = []
        for s in out.get("segments", []):
            s = _trim(s)
            s["start"], s["end"] = s["start"] + a, min(s["end"] + a, b)
            attempt.append(s)
        replaced = [s for s in segments if s["start"] < b and s["end"] > a]
        kept_words = _words([s for s in replaced if id(s) not in flagged_ids])
        if attempt and guard.acceptable(attempt) and _words(attempt) * 2 >= kept_words:
            block = replaced[0].get("block", 0) if replaced else 0
            for s in attempt:
                s["block"], s["repaired"] = block, True
            ids = {id(s) for s in replaced}
            merged = [s for s in segments if id(s) not in ids] + attempt
            merged.sort(key=lambda s: (s["start"], s["end"]))
            return merged, [{"kind": "repaired", "start": a, "end": b, "detail": ",".join(window.reasons)}], True
    issue = _unclear(flagged, window.reasons)
    return [s for s in segments if id(s) not in flagged_ids], [issue], False


def _speech_reference(segments, levels):
    """Median level of the frames inside lines with text, or None."""
    step = vad.FRAME_SECONDS
    inside = np.zeros(len(levels), dtype=bool)
    for s in segments:
        if s["text"].strip():
            inside[int(s["start"] / step): int(s["end"] / step)] = True
    return float(np.median(levels[inside])) if inside.any() else None


def _is_speech_level(levels, a, b, reference) -> bool:
    frames = levels[int(a / vad.FRAME_SECONDS): int(b / vad.FRAME_SECONDS)]
    return bool(frames.size) and float(np.mean(frames > reference - SPEECH_MARGIN_DB)) >= SPEECH_SHARE


def _decode_stretch(samples, engine, opts, total, a, b, lead):
    """One fresh decode of [a, b] with some lead-in. Returns the new lines inside [a, b], captions dropped."""
    a0, b0 = max(0.0, a - lead), min(total, b + REPAIR_TRAIL)
    piece = samples[int(a0 * SR): int(b0 * SR)]
    if not len(piece):
        return []
    out = engine.transcribe_array(piece, opts.language or None, **REPAIR_DECODE,
                                  **({"initial_prompt": opts.initial_prompt} if opts.initial_prompt else {}))
    lines = []
    for s in out.get("segments", []):
        s = _trim(s)
        s["start"], s["end"] = s["start"] + a0, min(s["end"] + a0, b0)
        if s["text"].strip() and a <= (s["start"] + s["end"]) / 2 <= b:
            lines.append(s)
    return guard.drop_non_speech(lines)[0]


def _fill_no_text(segments, blocks, issues, samples, engine, opts, total):
    """Decode again every stretch that has speech-level sound but no text. Returns (segments, new issues, count filled)."""
    levels = vad.frame_levels(samples, SR)
    reference = _speech_reference(segments, levels)
    if reference is None:
        return segments, [], 0
    targets = [(a, b, None) for a, b in guard.find_gaps(segments, blocks, issues)]
    targets += [(segments[i]["start"], segments[i]["end"], segments[i]) for i in guard.find_thin(segments)]
    new_issues, filled = [], 0
    for a, b, old in sorted(targets, key=lambda t: (t[0], t[1])):
        if not _is_speech_level(levels, a, b, reference):
            continue
        old_words, best, lines = _words([old]) if old else 0, 0, None
        for lead in REPAIR_LEAD:
            attempt = _decode_stretch(samples, engine, opts, total, a, b, lead)
            best = max(best, _words(attempt))
            if _words(attempt) >= old_words + MIN_GAIN_WORDS and guard.acceptable(attempt):
                lines = attempt
                break
        if lines is not None:
            block = old.get("block", 0) if old else next((n for n, (x, y) in enumerate(blocks) if x <= a <= y), 0)
            for s in lines:
                s["start"], s["end"] = min(max(s["start"], a), b), min(max(s["end"], a), b)
                s["block"], s["repaired"] = block, True
            segments = [s for s in segments if s is not old] + lines
            segments.sort(key=lambda s: (s["start"], s["end"]))
            new_issues.append({"kind": "repaired", "start": a, "end": b, "detail": "no_text"})
            filled += 1
        elif best >= old_words + MIN_GAIN_WORDS:
            new_issues.append({"kind": "unclear", "start": a, "end": b, "detail": "no_text"})
    return segments, new_issues, filled


def _finish(segments, issues, blocks, total, tempo, opts, engine, language, repaired_windows) -> dict:
    def rec(t: float) -> float:  # whisper time -> recording seconds
        return round(t * tempo, 3)

    duration = round(total * tempo, 3)
    segments = sorted(segments, key=lambda s: (s["start"], s["end"]))
    out_segments = []
    for i, s in enumerate(segments):
        out_segments.append({
            "id": i, "start": min(rec(s["start"]), duration), "end": min(rec(s["end"]), duration),
            "text": s["text"], "avg_logprob": s.get("avg_logprob"), "compression_ratio": s.get("compression_ratio"),
            "no_speech_prob": s.get("no_speech_prob"), "temperature": s.get("temperature") or 0.0,
            "block": s.get("block", 0), "repaired": bool(s.get("repaired"))})
    out_issues = sorted(({"kind": x["kind"], "start": rec(x["start"]), "end": rec(x["end"]), "detail": x["detail"]}
                         for x in issues), key=lambda x: (x["start"], x["end"]))
    out_blocks = [{"i": i, "start": rec(a), "end": rec(b)} for i, (a, b) in enumerate(blocks)]
    speech = round(sum(b - a for a, b in blocks) * tempo, 3)
    words = sum(len(s["text"].split()) for s in out_segments)
    wpm = round(words / (speech * opts.speed / 60.0), 1) if speech > 0 else 0.0
    flags = ["speed_looks_wrong"] if speech > 0 and (wpm > 230 or wpm < 60) else []
    span = lambda kind: round(sum(x["end"] - x["start"] for x in out_issues if x["kind"] == kind), 2)  # noqa: E731
    text = " ".join(s["text"].strip() for s in out_segments if s["text"].strip())
    return {
        "text": text, "segments": out_segments, "language": language, "v": 2, "speed": opts.speed,
        "tempo": round(tempo, 4), "duration": duration, "speech_seconds": speech, "blocks": out_blocks,
        "issues": out_issues, "corrections": [],
        "quality": {"words": words, "wpm": wpm, "unclear_seconds": span("unclear"),
                    "non_speech_seconds": span("non_speech"), "repaired_windows": repaired_windows, "flags": flags},
        "settings": {"engine": engine.name, "model": getattr(engine, "label", None) or engine.model,
                     "tempo_restore": opts.tempo_restore,
                     "repair": opts.repair},
    }
