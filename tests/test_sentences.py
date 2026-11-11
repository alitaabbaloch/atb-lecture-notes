import copy

import guard
import sentences


def parent(text, start=0.0, gaps=None, **kw):
    """A parent segment: every word lasts 0.3 s with 0.1 s between words. `gaps` = {word index: extra pause}."""
    gaps = gaps or {}
    words, t = [], start
    for i, w in enumerate(text.split()):
        t += gaps.get(i, 0.0)
        words.append({"word": " " + w, "start": round(t, 3), "end": round(t + 0.3, 3), "probability": 0.9})
        t += 0.4
    seg = {"start": start, "end": round(t, 3), "text": text, "avg_logprob": -0.2, "compression_ratio": 1.1,
           "no_speech_prob": 0.01, "temperature": 0.0, "words": words}
    seg.update(kw)
    return seg


def texts(lines):
    return [x["text"] for x in lines]


def test_two_sentences_give_two_lines():
    p = parent("zorb velt quin mape. tarn okle bruv senk.", start=2.0, avg_logprob=-0.4, temperature=0.2)
    lines = sentences.cut([p])
    assert texts(lines) == ["zorb velt quin mape.", "tarn okle bruv senk."]
    assert lines[0]["start"] == p["words"][0]["start"] and lines[0]["end"] == p["words"][3]["end"]
    assert lines[1]["start"] == p["words"][4]["start"] and lines[1]["end"] == p["words"][7]["end"]
    assert all(x["avg_logprob"] == -0.4 and x["temperature"] == 0.2 for x in lines)


def test_question_exclamation_and_closing_quote_end_sentences():
    p = parent('zorb velt quin mape? tarn okle bruv senk! fiz lorp "dane ruk." hob mil sarp denk')
    assert texts(sentences.cut([p])) == ["zorb velt quin mape?", "tarn okle bruv senk!", 'fiz lorp "dane ruk."',
                                         "hob mil sarp denk"]


def test_short_sentences_join_a_neighbour():
    mid = sentences.cut([parent("zorb velt quin mape. tarn okle. bruv senk fiz lorp.")])
    assert texts(mid) == ["zorb velt quin mape.", "tarn okle. bruv senk fiz lorp."]
    end = sentences.cut([parent("zorb velt quin mape. tarn okle.")])
    assert texts(end) == ["zorb velt quin mape. tarn okle."]
    assert texts(sentences.cut([parent("zorb velt. quin.")])) == ["zorb velt. quin."]


def test_long_sentence_is_cut_at_the_longest_pause_in_range():
    words = " ".join(f"w{i}" for i in range(70))
    p = parent(words, gaps={35: 2.0, 10: 5.0})  # the pause at position 11 is outside the allowed range
    lines = sentences.cut([p])
    sizes = [len(x["text"].split()) for x in lines]
    assert max(sizes) <= 40 and sum(sizes) == 70
    assert sizes == [35, 35]
    assert lines[1]["text"].startswith("w35 ")


def test_nothing_understood_is_dropped_and_ids_stay_dense():
    segs = [parent("zorb velt quin mape."), parent("nan", 30), parent(" Nan. ", 60), parent("", 90),
            parent("tarn okle bruv senk.", 120)]
    lines = sentences.cut(segs)
    assert texts(lines) == ["zorb velt quin mape.", "tarn okle bruv senk."]
    assert [x["id"] for x in lines] == [0, 1]


def test_parent_without_words_is_one_line():
    p = {"start": 5.0, "end": 17.5, "text": " Zorb velt. Quin mape. ", "avg_logprob": -0.3}
    for words in ({}, {"words": []}):
        lines = sentences.cut([{**p, **words}])
        assert len(lines) == 1
        assert lines[0]["text"] == "Zorb velt. Quin mape." and (lines[0]["start"], lines[0]["end"]) == (5.0, 17.5)


def test_stretched_last_word_is_capped():
    p = parent("zorb velt quin mape.")
    p["words"][-1]["end"] = p["words"][-1]["start"] + 6.0
    p["end"] = p["words"][-1]["end"]
    lines = sentences.cut([p])
    assert lines[0]["end"] == round(p["words"][-1]["start"] + 1.5, 3)


def test_timing_rules_on_messy_word_times():
    def w(word, start, end):
        return {"word": " " + word, "start": start, "end": end}

    words = [w("zorb", 1.0, 1.6), w("velt", 1.2, 1.3), w("quin", 1.1, 1.2), w("mape.", 1.5, 1.45),  # overlap, backwards
             w("tarn", 1.0, 1.05), w("okle", 1.0, 1.0), w("bruv", 0.9, 0.95), w("senk.", 1.0, 1.0),  # all before the end
             w("fiz", 5.0, 5.01), w("lorp", 5.0, 5.01), w("dane", 5.0, 5.01), w("ruk.", 9.9, 14.0)]  # past the parent end
    p = {"start": 1.0, "end": 10.0, "text": "x y z", "words": words}
    lines = sentences.cut([p])
    assert lines
    prev_end = p["start"]
    for x in lines:
        assert x["start"] >= prev_end - 1e-9
        assert x["end"] >= x["start"] and x["end"] <= p["end"] + 1e-9
        assert x["end"] - x["start"] >= 0.1 - 1e-9
        prev_end = x["end"]


def test_two_parents_in_a_row_and_input_untouched():
    segs = [parent("zorb velt quin mape. tarn okle bruv senk.", 0.0), parent("fiz lorp dane ruk.", 5.0)]
    before = copy.deepcopy(segs)
    lines = sentences.cut(segs)
    assert segs == before
    assert texts(lines)[-1] == "fiz lorp dane ruk."
    assert lines[2]["start"] >= lines[1]["end"]
    assert [x["id"] for x in lines] == [0, 1, 2]


def test_missing_fields_and_no_words_key():
    p = parent("zorb velt quin mape.")
    del p["temperature"], p["avg_logprob"]
    lines = sentences.cut([p])
    assert lines[0]["temperature"] == 0.0 and lines[0]["avg_logprob"] is None
    assert all("words" not in x for x in sentences.cut([parent("zorb velt quin mape."), {"start": 0, "end": 1, "text": "a b"}]))
    assert set(lines[0]) == {"id", "start", "end", "text", "avg_logprob", "compression_ratio", "no_speech_prob",
                             "temperature"}


def test_phrase_repeated_in_real_speech_is_cut_not_kept_whole():
    sents = []
    for i in range(8):
        phrase = "alpha beta gamma " if i % 2 == 0 else ""
        sents.append(phrase + " ".join(f"w{i}x{j}" for j in range(8)) + ".")
    text = " ".join(sents)
    assert len(text.split()) >= 70 and guard.is_loop_text(text)
    assert len(sentences.cut([parent(text)])) > 1


def test_stammer_start_is_cut_not_kept_whole():
    text = "I I I I I have " + " ".join(f"w{j}" for j in range(8)) + ". " + " ".join(f"v{j}" for j in range(15)) + "."
    assert 25 <= len(text.split()) <= 35 and guard.is_loop_text(text)
    assert len(sentences.cut([parent(text)])) > 1


def test_loop_at_the_end_of_a_parent_does_not_spoil_the_real_lines():
    first = " ".join(f"a{j}" for j in range(10)) + "."
    second = " ".join(f"b{j}" for j in range(10)) + "."
    lines = sentences.cut([parent(first + " " + second + " " + "kfc, " * 30)])
    assert texts(lines)[:2] == [first, second]
    assert len(lines) > 2 and all(len(x["text"].split()) <= 40 for x in lines)
    flags = guard._flags(lines)
    assert 0 not in flags and 1 not in flags
    assert all("loop" in flags[i] for i in range(2, len(lines)))


def test_unpunctuated_loop_becomes_flagged_lines_of_at_most_40_words():
    lines = sentences.cut([parent("kfc " * 100)])
    assert len(lines) > 2 and all(len(x["text"].split()) <= 40 for x in lines)
    flags = guard._flags(lines)
    assert all("loop" in flags[i] for i in range(len(lines)))


def test_repeated_short_sentence_gives_identical_flagged_lines():
    lines = sentences.cut([parent("Thank you. " * 30)])
    assert len(set(texts(lines))) == 1 and len(lines) > 3
    assert len(guard._flags(lines)) >= len(lines) - 2


def test_nan_inside_a_line_becomes_unknown_mark():
    lines = sentences.cut([parent("nan de raha hai. Ok theek hai yeh.")])
    assert texts(lines) == ["(?) de raha hai.", "Ok theek hai yeh."]
    mid = sentences.cut([parent("zorb velt quin mape nan. tarn okle bruv senk.")])
    assert texts(mid)[0].endswith("mape (?).") and len(mid) == 2
    assert texts(sentences.cut([parent("zorb velt Nan quin mape.")])) == ["zorb velt (?) quin mape."]


def test_repeated_nan_parent_is_dropped():
    assert sentences.cut([parent("nannan."), parent("nan nan nan.", 30)]) == []


def test_sentence_of_only_nan_is_dropped():
    lines = sentences.cut([parent("zorb velt quin mape. nan nan nan nan. tarn okle bruv senk.")])
    assert texts(lines) == ["zorb velt quin mape.", "tarn okle bruv senk."]


def test_punctuation_run_is_collapsed_and_bare_marks_dropped():
    lines = sentences.cut([parent("zorb velt quin location.!!!!!!!! tarn okle bruv senk. !!!!!! wait......")])
    assert texts(lines)[0] == "zorb velt quin location.!"
    assert "!!!" not in " ".join(texts(lines)) and texts(lines)[-1].endswith("senk. wait.")


def test_single_marks_are_untouched():
    assert texts(sentences.cut([parent("kya hua? sab theek hai! ok.")])) == ["kya hua? sab theek hai! ok."]
