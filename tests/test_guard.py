import guard


def seg(start, end, text, **kw):
    return {"start": start, "end": end, "text": text, "avg_logprob": -0.3, "temperature": 0.0, **kw}


def test_loop_rules():
    assert not guard.is_loop_text("a b c")  # too short
    assert not guard.is_loop_text("ruko ruko ruko ruko aur phir chalo ab ghar")  # 4 repeats pass
    assert guard.is_loop_text("ruko ruko ruko ruko ruko aur phir chalo ab ghar")  # 5 fail
    assert guard.is_loop_text("one two three one two three one two three one two three x y")  # a 3-word sequence x4
    assert not guard.is_loop_text("one two three one two three one two three x y z w")  # only x3
    assert guard.is_loop_text("a b a b a b a b a b a b")  # unique ratio below 0.35
    assert not guard.is_loop_text("the quick brown fox jumps over the lazy dog today")
    assert guard.is_loop_text("وہ " * 30)


def test_foreign_script():
    assert not guard.has_foreign_script("یہ اردو ہے with English 123 and punctuation!")
    assert guard.has_foreign_script("hello 中文")
    assert guard.has_foreign_script("नमस्ते")  # Devanagari
    assert not guard.has_foreign_script("café 12:30 ... ؟")


def test_captions():
    for text in ("موسیقی", " [Music] ", "(موسیقی)", "Applause.", "تالیاں", "♪", "♪ ♪"):
        assert guard.is_caption(text), text
    assert not guard.is_caption("music is great")
    assert not guard.is_caption("موسیقی اور تعلیم")
    assert not guard.is_caption("")
    assert guard.is_caption("nan.") and guard.is_caption(" Nan ") and not guard.is_caption("nan bread recipe")


def test_drop_non_speech_merges_consecutive():
    segs = [seg(0, 4, "real words here"), seg(4, 6, "موسیقی"), seg(6, 8, "[Music]"), seg(8, 12, "more real"),
            seg(30, 31, "موسیقی")]
    kept, issues = guard.drop_non_speech(segs)
    assert [s["text"] for s in kept] == ["real words here", "more real"]
    assert issues == [{"kind": "non_speech", "start": 4, "end": 8, "detail": "caption"},
                      {"kind": "non_speech", "start": 30, "end": 31, "detail": "caption"}]


def test_identical_consecutive_segments_flag_from_the_third():
    segs = [seg(0, 4, "same line"), seg(4, 8, "same line"), seg(8, 12, "same line"), seg(12, 16, "other")]
    windows = guard.find_bad_windows(segs)
    assert len(windows) == 1 and windows[0].segment_ids == [2] and windows[0].reasons == ["repeat"]
    assert guard.find_bad_windows(segs[:2]) == []


def test_zero_length_cluster():
    zero = [seg(10, 10, "x"), seg(12, 12, "y"), seg(14, 14.05, "z")]
    windows = guard.find_bad_windows([seg(0, 4, "fine")] + zero)
    assert len(windows) == 1 and windows[0].segment_ids == [1, 2, 3] and windows[0].reasons == ["zero_length"]
    spread = [seg(0, 0, "x"), seg(12, 12, "y"), seg(24, 24, "z")]  # never three inside 10 s
    assert guard.find_bad_windows(spread) == []


def test_foreign_and_loop_windows_are_grouped_by_distance():
    segs = [seg(0, 4, "fine words"), seg(4, 8, "word " * 20), seg(8, 12, "中文"), seg(40, 44, "ok text"),
            seg(60, 64, "word " * 20)]
    windows = guard.find_bad_windows(segs)
    assert [(w.start, w.end) for w in windows] == [(4, 12), (60, 64)]  # 12 -> 60 is more than 30 s apart
    assert windows[0].reasons == ["foreign_script", "loop"] and windows[0].segment_ids == [1, 2]
    near = [seg(0, 4, "word " * 20), seg(33, 37, "word " * 20)]  # 29 s apart: one window
    assert len(guard.find_bad_windows(near)) == 1
    far = [seg(0, 4, "word " * 20), seg(35, 37, "word " * 20)]  # 31 s apart: two
    assert len(guard.find_bad_windows(far)) == 2


def test_acceptable():
    good = [seg(0, 4, "fine text here"), seg(4, 8, "more fine text")]
    assert guard.acceptable(good)
    assert not guard.acceptable(good + [seg(8, 12, "word " * 20)])
    assert not guard.acceptable([seg(0, 4, "fine text", avg_logprob=-1.4)])
    assert guard.acceptable([seg(0, 1, "x y", avg_logprob=-1.6), seg(1, 10, "fine text", avg_logprob=-0.5)])  # weighted
    assert not guard.acceptable([seg(0, 4, "fine text", temperature=0.6)])
    assert guard.acceptable([seg(0, 4, "fine text", temperature=0.4)])
    assert guard.acceptable([])


def _line(a, b, text="one two three four five six"):
    return {"start": a, "end": b, "text": text}


def test_find_gaps():
    blocks = [(0.0, 40.0)]
    assert guard.find_gaps([_line(0, 10), _line(22, 40)], blocks) == [(10, 22)]
    assert guard.find_gaps([_line(0, 10), _line(15, 40)], blocks) == []
    for kind in ("non_speech", "unclear"):
        assert guard.find_gaps([_line(0, 10), _line(22, 40)], blocks, [{"kind": kind, "start": 10, "end": 22}]) == []
    assert guard.find_gaps([_line(0, 10), _line(12, 28)], blocks) == [(28, 40)]  # the tail of the block counts
    assert guard.find_gaps([_line(0, 10)], [(0.0, 10.0), (30.0, 40.0)]) == [(30.0, 40.0)]  # between blocks: not a gap
    assert guard.find_gaps([_line(0, 10), _line(10, 22, " "), _line(22, 40)], blocks) == [(10, 22)]  # empty text
    assert guard.find_gaps([_line(0, 10), _line(22, 40)], blocks, [{"kind": "repaired", "start": 10, "end": 22}]) == [(10, 22)]


def test_find_thin():
    normal = [_line(i * 4, i * 4 + 4, "a b c d e f g h i j k l") for i in range(25)]
    assert guard.find_thin(normal + [_line(100, 120, "one two three four")]) == [25]
    assert guard.find_thin(normal[:10] + [_line(100, 120, "one two three four")]) == []
    assert guard.find_thin(normal + [_line(100, 120, " ".join(["w"] * 60))]) == []


def test_char_loop_inside_one_token():
    for text in ("cyncyncyncyncyncyncyncyncyn", "hahahahahahahaha", "aaaaaaaa", "kya hua cyncyncyncyncyncyncyncyn"):
        assert guard.has_char_loop(text), text
    for text in ("banana", "Mississippi", "aa gaya", "100000000", "ha ha ha ha", ""):
        assert not guard.has_char_loop(text), text


def test_char_loop_line_is_flagged_loop():
    segs = [seg(0, 4, "fine words here"), seg(4, 8, "ok cyncyncyncyncyncyncyncyncyn then")]
    windows = guard.find_bad_windows(segs)
    assert len(windows) == 1 and windows[0].segment_ids == [1] and windows[0].reasons == ["loop"]
