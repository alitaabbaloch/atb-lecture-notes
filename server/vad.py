"""Energy-based speech detection and block building. numpy only, no model.

A "span" is a stretch of the recording with speech-like energy. A "block" is a continuous stretch of the
recording that is sent to Whisper in one call.
"""
import numpy as np

FRAME_SECONDS = 0.02
FLOOR_MARGIN_DB = 6.0   # threshold = 2nd percentile of frame levels + this
MIN_THRESHOLD_DB = -80.0  # ...but never below this. Only long true silence is cut out; levels vary 30 dB between recordings
MIN_RUN_FRAMES = 2
MAX_THRESHOLD_DB = -60.0  # a steady tone or hum has p2 + 6 above its own level; never let the threshold pass this
JOIN_GAP = 0.7
PAD = 0.3
MIN_BLOCK = 0.6


def _runs(active: np.ndarray) -> list[tuple[int, int]]:
    """(first frame, one past last frame) for every run of True."""
    if not active.size:
        return []
    padded = np.concatenate(([False], active, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def frame_levels(samples: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Level in dB of every 20 ms frame (an empty array for less than one frame)."""
    frame = int(sr * FRAME_SECONDS)
    n = len(samples) // frame if frame else 0
    if n == 0:
        return np.zeros(0)
    x = np.asarray(samples[: n * frame], dtype=np.float64).reshape(n, frame)
    rms = np.sqrt(np.mean(x * x, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-10))


def energy_spans(samples: np.ndarray, sr: int = 16000) -> list[tuple[float, float]]:
    """The built-in detector."""
    total = len(samples) / sr if sr else 0.0
    level = frame_levels(samples, sr)
    if not level.size:
        return []
    threshold = min(max(float(np.percentile(level, 2)) + FLOOR_MARGIN_DB, MIN_THRESHOLD_DB), MAX_THRESHOLD_DB)
    spans = [(a * FRAME_SECONDS, b * FRAME_SECONDS) for a, b in _runs(level > threshold)
             if b - a >= MIN_RUN_FRAMES]
    joined: list[list[float]] = []
    for start, end in spans:
        if joined and start - joined[-1][1] < JOIN_GAP:
            joined[-1][1] = end
        else:
            joined.append([start, end])
    padded: list[list[float]] = []
    for start, end in joined:
        start, end = max(0.0, start - PAD), min(total, end + PAD)
        if padded and start <= padded[-1][1]:
            padded[-1][1] = max(padded[-1][1], end)
        else:
            padded.append([start, end])
    return [(a, b) for a, b in padded if b > a]


def detector():
    """Extension point: return the function that finds speech spans, `f(samples, sr) -> [(start, end)]`.

    A neural detector can be plugged in here later; callers only use speech_spans()."""
    return energy_spans


def speech_spans(samples: np.ndarray, sr: int = 16000) -> list[tuple[float, float]]:
    return detector()(samples, sr)


def build_blocks(spans, total_seconds: float, target: float = 300.0, hard_max: float = 480.0,
                 long_gap: float = 3.0) -> list[tuple[float, float]]:
    """Group spans into blocks. Gaps longer than long_gap are outside every block."""
    spans = [(float(a), float(b)) for a, b in sorted(spans) if b > a]
    stretches: list[list[tuple[float, float]]] = []
    for span in spans:
        if stretches and span[0] - stretches[-1][-1][1] <= long_gap:
            stretches[-1].append(span)
        else:
            stretches.append([span])
    blocks: list[tuple[float, float]] = []
    for stretch in stretches:
        blocks.extend(_cut_stretch(stretch, target, hard_max))
    out = []
    for start, end in blocks:
        start, end = max(0.0, start), min(float(total_seconds), end)
        if end - start >= MIN_BLOCK:
            out.append((start, end))
    return out


def _cut_stretch(stretch, target: float, hard_max: float) -> list[tuple[float, float]]:
    end_all = stretch[-1][1]
    blocks = []
    cur = stretch[0][0]  # start of the block being built
    first = 0            # index of the first span that belongs to it
    while end_all - cur > 1.2 * target and end_all - cur > 0:
        # gaps (pauses) that start after cur, as (gap start, gap end, index of the span after the gap)
        gaps = [(stretch[i - 1][1], stretch[i][0], i) for i in range(max(first, 1), len(stretch))
                if stretch[i][0] > stretch[i - 1][1]]
        near = [g for g in gaps if cur + 0.8 * target <= g[0] <= cur + 1.2 * target]
        anywhere = [g for g in gaps if cur + 0.5 * target <= g[0] <= cur + hard_max]
        pick = max(near, key=lambda g: g[1] - g[0]) if near else (
            min(anywhere, key=lambda g: abs(g[0] - (cur + target))) if anywhere else None)
        if pick is None:  # unbroken speech: cut by force
            cut = cur + min(target, hard_max)
            blocks.append((cur, cut))
            cur = cut
            first = next((i for i, s in enumerate(stretch) if s[1] > cur), len(stretch) - 1)
            continue
        blocks.append((cur, pick[0]))
        cur, first = pick[1], pick[2]
    blocks.append((cur, end_all))
    return blocks
