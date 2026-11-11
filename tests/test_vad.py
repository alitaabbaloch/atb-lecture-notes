import numpy as np

import vad

SR = 16000


def signal(parts):
    """parts: ('s', seconds) silence or ('n', seconds) noise burst."""
    rng = np.random.default_rng(1)
    out = []
    for kind, seconds in parts:
        n = int(seconds * SR)
        out.append(np.zeros(n, np.float32) if kind == "s" else (rng.standard_normal(n) * 0.1).astype(np.float32))
    return np.concatenate(out)


def close(a, b, tol=0.1):
    return abs(a - b) <= tol


def test_silence_and_empty_give_no_spans():
    assert vad.speech_spans(np.zeros(SR * 5, np.float32)) == []
    assert vad.speech_spans(np.zeros(0, np.float32)) == []
    assert vad.speech_spans(np.zeros(100, np.float32)) == []


def test_bursts_with_three_gaps():
    x = signal([("n", 3), ("s", 0.3), ("n", 3), ("s", 1.5), ("n", 3), ("s", 10), ("n", 3)])
    spans = vad.speech_spans(x)
    assert len(spans) == 3  # the 0.3 s pause is joined, the 1.5 s and 10 s pauses are not
    assert close(spans[0][0], 0.0) and close(spans[0][1], 6.6)  # padded 0.3 s each side, clipped at 0
    assert close(spans[1][0], 7.5) and close(spans[1][1], 11.1)
    assert close(spans[2][0], 20.5) and close(spans[2][1], 23.8)
    total = len(x) / SR
    blocks = vad.build_blocks(spans, total)
    assert len(blocks) == 2  # the 10 s gap is longer than long_gap and lies outside every block
    assert close(blocks[0][0], 0.0) and close(blocks[0][1], 11.1)
    assert close(blocks[1][0], 20.5) and close(blocks[1][1], 23.8)


def test_one_frame_blips_are_ignored():
    x = signal([("s", 2), ("n", 0.02), ("s", 2)])
    assert vad.speech_spans(x) == []


def level_signal(parts, seed=2):
    """parts: (dBFS rms, seconds) noise stretches; dBFS None-free, exact constant level when 'const' is True."""
    rng = np.random.default_rng(seed)
    out = []
    for db, seconds in parts:
        n = int(seconds * SR)
        out.append((rng.standard_normal(n) * 10 ** (db / 20)).astype(np.float32))
    return np.concatenate(out)


def test_quiet_recording_is_kept():
    x = level_signal([(-76, 3), (-50, 4), (-76, 2), (-50, 4), (-76, 3)])
    spans = vad.speech_spans(x)
    blocks = vad.build_blocks(spans, len(x) / SR)
    assert len(blocks) == 1 and close(blocks[0][0], 2.7) and close(blocks[0][1], 13.3)  # bursts plus 0.3 s padding
    assert any(a <= 3.5 and b >= 6.5 for a, b in spans) and any(a <= 9.5 and b >= 12.5 for a, b in spans)


def test_gated_call_audio_keeps_speech_and_cuts_true_silence():
    gap = np.full(int(60 * SR), 10 ** (-112 / 20), np.float32)  # constant -112 dB: a break or paused video
    gated = lambda: level_signal([(-25, 3), (-90, 1), (-25, 3), (-90, 1.5)])  # noqa: E731
    x = np.concatenate([gated(), gap, gated()])
    blocks = vad.build_blocks(vad.speech_spans(x), len(x) / SR)
    assert len(blocks) == 2
    assert close(blocks[0][0], 0, 0.1) and blocks[0][1] < 12.0
    assert blocks[1][0] > 68.0  # the 60 s stretch is outside every block
    assert sum(b - a for a, b in blocks) > 14.0


def test_steady_tone_is_kept():
    t = np.arange(SR * 4) / SR
    tone = (0.12 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    spans = vad.speech_spans(tone)
    assert len(spans) == 1 and close(spans[0][0], 0) and close(spans[0][1], 4.0)


def test_long_gap_excludes_and_short_gap_keeps():
    assert vad.build_blocks([(0, 10), (13.5, 20)], 20) == [(0, 10), (13.5, 20)]
    assert vad.build_blocks([(0, 10), (12.5, 20)], 20) == [(0, 20)]


def test_tiny_blocks_are_dropped():
    assert vad.build_blocks([(5, 5.4)], 10) == []


def test_cut_near_target_at_the_longest_pause():
    spans = [(0, 25), (27, 29.5), (30, 100)]
    blocks = vad.build_blocks(spans, 100, target=30, hard_max=48, long_gap=3)
    assert blocks[0] == (0, 25) and blocks[1][0] == 27  # the 2 s pause wins over the 0.5 s pause
    spans = [(i * 10, i * 10 + 9) for i in range(10)]
    blocks = vad.build_blocks(spans, 100, target=30, hard_max=48)
    assert blocks[0] == (0, 29) and blocks[1][0] == 30


def test_hard_maximum_with_unbroken_speech():
    blocks = vad.build_blocks([(0, 1000)], 1000)
    assert max(b - a for a, b in blocks) <= 480
    assert blocks[0][0] == 0 and blocks[-1][1] == 1000
    assert all(blocks[i][1] == blocks[i + 1][0] for i in range(len(blocks) - 1))
    blocks = vad.build_blocks([(0, 100), (100.5, 1000)], 1000)
    assert max(b - a for a, b in blocks) <= 480


def test_extension_point():
    assert vad.detector() is vad.energy_spans


def test_frame_levels_of_a_tone_and_of_too_little_audio():
    t = np.arange(16000) / 16000
    x = (0.1 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    levels = vad.frame_levels(x)
    assert len(levels) == 50
    assert np.all(np.abs(levels - (20 * np.log10(0.1 / np.sqrt(2)))) < 1.0)
    assert vad.frame_levels(x[:100]).size == 0
