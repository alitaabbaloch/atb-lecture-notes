import json
from pathlib import Path

import glossary

STARTER = Path(__file__).resolve().parent.parent / "glossaries" / "seo-starter.txt"


def g(text: str) -> glossary.Glossary:
    return glossary.parse(text)


def test_parse_comments_blank_lines_and_plain_terms():
    gl = g("# comment\n\nkeyword = کیورڈ | کی ورڈ   # trailing\nSearch Console\n")
    assert gl.entries == [("keyword", ["کیورڈ", "کی ورڈ"]), ("Search Console", [])]
    assert gl.terms == ["keyword", "Search Console"]


def test_single_token_and_punctuation_kept():
    new, corr = g("keyword = کیورڈ").apply("یہ کیورڈ، ہے۔ (کیورڈ) کیورڈ")
    assert new == "یہ keyword، ہے۔ (keyword) keyword"
    assert corr == [{"from": "کیورڈ", "to": "keyword", "count": 3}]


def test_multi_token_variant_and_longest_first():
    gl = g("keyword = کی ورڈ | key word\nkeywords = key words\nkey = کی")
    new, _ = gl.apply("کی ورڈ and key words and key word")
    assert new == "keyword and keywords and keyword"


def test_latin_variants_are_case_insensitive_arabic_exact():
    gl = g("ChatGPT = chat gpt | چیٹ جی پی ٹی\nkeyword = کیورڈ")
    assert gl.apply("use Chat GPT now")[0] == "use ChatGPT now"
    # zero-width characters inside an Arabic-script token are ignored when matching
    assert gl.apply("کیوڑڈ")[0] == "کیوڑڈ"  # Arabic-script variants match exactly: a different letter is another word
    assert gl.apply("ک‌یورڈ")[0] == "keyword"


def test_no_match_inside_longer_token_or_across_punctuation():
    gl = g("keyword = کیورڈ | key word")
    assert gl.apply("کیورڈز")[0] == "کیورڈز"
    assert gl.apply("monkey words")[0] == "monkey words"
    assert gl.apply("key، word")[0] == "key، word"
    assert gl.apply("key  word")[0] == "keyword"  # whitespace of any width between the tokens


def test_canonical_token_left_alone_and_unchanged_text_has_no_corrections():
    gl = g("Google = گوگل | google")
    assert gl.apply("Google and Google") == ("Google and Google", [])


def test_apply_to_result_keeps_raw_text_and_merges_corrections():
    gl = g("keyword = کیورڈ")
    result = {"text": "a کیورڈ b c", "language": "ur",
              "segments": [{"text": "a کیورڈ", "start": 0, "end": 1}, {"text": "b c", "start": 1, "end": 2},
                           {"text": " کیورڈ x", "start": 2, "end": 3}],
              "corrections": [{"from": "x", "to": "y", "count": 1}]}
    out = glossary.apply_to_result(result, gl)
    assert out["segments"][0]["raw_text"] == "a کیورڈ" and out["segments"][0]["text"] == "a keyword"
    assert "raw_text" not in out["segments"][1]
    assert {"from": "کیورڈ", "to": "keyword", "count": 2} in out["corrections"]
    assert {"from": "x", "to": "y", "count": 1} in out["corrections"]
    assert out["text"] == "a keyword b c keyword x"


def test_apply_to_result_without_changes_is_untouched():
    result = {"text": "a b", "segments": [{"text": "a b", "start": 0, "end": 1}]}
    before = json.dumps(result)
    assert glossary.apply_to_result(result, g("keyword = کیورڈ")) is result
    assert json.dumps(result) == before


def test_prompt_is_short_and_latin():
    gl = glossary.load(STARTER)
    prompt = gl.prompt()
    assert len(prompt) <= 600 and "keyword" in prompt and "SEO" in prompt
    assert len(gl.prompt(max_chars=60)) <= 60


def test_ensure_course_glossary_copies_once_never_overwrites(tmp_path):
    path = glossary.ensure_course_glossary(tmp_path, "my-course", "seo-starter")
    assert path == tmp_path / "notes" / "my-course" / "glossary.txt"
    assert path.read_text(encoding="utf-8") == STARTER.read_text(encoding="utf-8")
    path.write_text("mine = x\n", encoding="utf-8")
    assert glossary.ensure_course_glossary(tmp_path, "my-course", "seo-starter") == path
    assert path.read_text(encoding="utf-8") == "mine = x\n"


def test_empty_starter_copies_nothing(tmp_path):
    path = glossary.ensure_course_glossary(tmp_path, "c", "")
    assert not path.exists() and glossary.load(path).entries == []


def test_every_starter_line_parses():
    lines = [l for l in STARTER.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    gl = glossary.load(STARTER)
    assert len(gl.entries) == len(lines) == 59
    for (canonical, variants), line in zip(gl.entries, lines):
        assert line.startswith(canonical) and canonical
        assert all(variants) and ("=" in line) == bool(variants)
    assert ("KD", []) in gl.entries and ("Claude", []) in gl.entries


def test_starter_variants_are_not_prefix_ambiguous():
    """No variant starts with the function words کے or کی: "اس کی ورڈ فائل" (its Word file) must not become
    "keyword", and "کے ڈی آر" (of DR) must not become "KD"."""
    for _, variants in glossary.load(STARTER).entries:
        for v in variants:
            assert v.split()[0] not in ("کے", "کی"), v


def test_starter_fixes_the_keyword_family():
    gl = glossary.load(STARTER)
    new, _ = gl.apply("ہمارا کیورڈ، اس کی ورڈ فائل اور کیورڈز اور key words")
    assert new == "ہمارا keyword، اس کی ورڈ فائل اور keywords اور keywords"


# ---- suggestions -------------------------------------------------------------------------------------------

class FakeLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def chat(self, system, user, *, max_tokens=8000, purpose="notes"):
        self.calls.append((purpose, user))
        return self.answer


def result_with(tokens_by_seg):
    return {"segments": [{"text": t, "start": i, "end": i + 1} for i, t in enumerate(tokens_by_seg)]}


def test_candidates_need_two_sightings_and_skip_known_and_common():
    gl = g("keyword = کیورڈ")
    res = result_with(["یہ ہے سیمرش ٹول", "سیمرش اچھا ہے", "کیورڈ کیورڈ", "ہے ہے ہے", "ab ab", "رینک ٹریکر", "رینک ٹریکر"])
    found = {c["token"]: c for c in glossary.candidates(res, gl)}
    assert "سیمرش" in found and found["سیمرش"]["count"] == 2
    assert "رینک ٹریکر" in found
    assert "کیورڈ" not in found and "ہے" not in found
    assert all(len(c["example"]) <= 80 for c in found.values())


def test_suggest_variants_merges_into_file_and_never_applies(tmp_path):
    gl = g("keyword = کیورڈ")
    res = result_with(["سیمرش ٹول", "سیمرش دوبارہ"])
    llm = FakeLLM('```json\n[{"variant": "سیمرش", "canonical": "Semrush", "confidence": 0.9},'
                  ' {"variant": "not-a-candidate", "canonical": "X", "confidence": 1}]\n```')
    out = glossary.suggest_variants(res, gl, llm, course_dir=tmp_path)
    assert [s["variant"] for s in out] == ["سیمرش"] and out[0]["status"] == "pending"
    saved = json.loads((tmp_path / "glossary-suggestions.json").read_text(encoding="utf-8"))
    assert saved[0]["canonical"] == "Semrush" and saved[0]["count"] == 2 and saved[0]["status"] == "pending"
    saved[0]["status"] = "accepted"
    (tmp_path / "glossary-suggestions.json").write_text(json.dumps(saved), encoding="utf-8")
    glossary.suggest_variants(res, gl, llm, course_dir=tmp_path)
    again = json.loads((tmp_path / "glossary-suggestions.json").read_text(encoding="utf-8"))
    assert len(again) == 1 and again[0]["status"] == "accepted" and again[0]["count"] == 4
    assert gl.apply("سیمرش")[0] == "سیمرش"
    assert llm.calls[0][0] == "suggest"


def test_suggest_without_candidates_makes_no_call():
    llm = FakeLLM("[]")
    assert glossary.suggest_variants(result_with(["یہ ہے"]), g("a = b"), llm) == [] and llm.calls == []
