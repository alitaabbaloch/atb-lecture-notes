"""Per-course glossary: fixes the spelling of English terms in Urdu transcripts and suggests new variants.

File format (notes/<course-slug>/glossary.txt), one entry per line, `#` starts a comment:
    keyword = کیورڈ | کی ورڈ | key word
    Search Console
Left of `=` is the canonical spelling, right of it the variants. A line without `=` is a term with no variants.
"""
import json
import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("atb")

REPO_ROOT = Path(__file__).resolve().parent.parent
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍‎‏‪‫‬‭‮⁠﻿"))
URDU_INTRO = "یہ اردو لیکچر ہے، انگریزی اصطلاحات:"

# Very common Urdu function words, never worth suggesting as English terms.
URDU_COMMON = frozenset("""
ہے ہیں تھا تھی تھے ہو ہوں ہوتا ہوتی ہوتے ہوگا ہوگی ہوگیا ہوئی ہوئے ہوا کا کی کے کو میں سے نے پر اور یا
یہ وہ اس ان جو جس جن کہ کیا کیوں کیسے کب کہاں کون کیونکہ تو بھی ہی نہیں نہ بس اب پھر
پہلے بعد ساتھ لیے لئے طرح والا والی والے ایک دو تین چار پانچ کچھ سب بہت زیادہ کم تھوڑا
جب تب جہاں وہاں یہاں ادھر اُدھر ادھر ابھی کبھی ہمیشہ بھی اگر مگر لیکن بلکہ جی ہاں ٹھیک
اچھا چلیں چلو دیکھیں دیکھو دیکھ کریں کرنا کرتے کرتا کرتی کرو کر کیا کرکے کرنے کی گیا گئی گئے
آپ ہم میں مجھے مجھ تمہیں تم آپکو ہمیں انہیں اسے اسکو اپنا اپنی اپنے اپنے
رہا رہی رہے رہتا رہتی رہتے سکتا سکتی سکتے چاہیے چاہئے چاہتے چاہتا چاہتی
دیا دی دے دیتا دیتے دینا لیا لی لے لیتا لیتے لینا ملا ملتا ملتی
آتا آتی آتے آنا آیا آئی آئے جاتا جاتی جاتے جانا جائے جائیں
ہوجاتا ہوجاتی ہوجائے ہوسکتا بنتا بنتی بنتے بنانا بنا بناتے
کوئی کسی کیوں سا سی سے ایسا ایسی ایسے جیسے جیسا کیسی ویسے
اوپر نیچے اندر باہر ساتھ بغیر لیے دوران درمیان مطلب یعنی
""".split())


def clean(text: str) -> str:
    return text.translate(ZERO_WIDTH)


def norm(token: str) -> str:
    """Comparison form of a token: no zero-width characters, case folded (Arabic script is unaffected)."""
    return clean(token).casefold()


def _is_word_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LNM"


def split_token(token: str) -> tuple[str, str, str]:
    """(leading punctuation, core, trailing punctuation). Marks stay with the core."""
    start, end = 0, len(token)
    while start < end and not _is_word_char(token[start]):
        start += 1
    while end > start and not _is_word_char(token[end - 1]):
        end -= 1
    return token[:start], token[start:end], token[end:]


def is_arabic_script(text: str) -> bool:
    letters = [c for c in clean(text) if c.isalpha()]
    return bool(letters) and all("ARABIC" in unicodedata.name(c, "") for c in letters)


@dataclass
class Glossary:
    entries: list = field(default_factory=list)  # (canonical, [variants])

    def __post_init__(self):
        self._index = None

    def _build(self):
        index: dict[str, list] = {}
        seen = set()
        for canonical, variants in self.entries:
            for variant in variants:
                key = tuple(norm(t) for t in variant.split())
                if not key or key in seen:
                    continue
                seen.add(key)
                index.setdefault(key[0], []).append((key, canonical))
        for options in index.values():
            options.sort(key=lambda o: -len(o[0]))  # longest variant first
        self._index = index

    @property
    def terms(self) -> list[str]:
        return [canonical for canonical, _ in self.entries]

    def variant_set(self) -> set[str]:
        """All variants as normalised strings (tokens joined by one space)."""
        return {" ".join(norm(t) for t in v.split()) for _, vs in self.entries for v in vs}

    def apply(self, text: str) -> tuple[str, list]:
        """Replace known variants by their canonical spelling. Returns (new text, corrections)."""
        if self._index is None:
            self._build()
        if not self._index or not text:
            return text, []
        parts = re.split(r"(\s+)", text)  # words at even positions, whitespace at odd ones
        words = [(i, *split_token(parts[i])) for i in range(0, len(parts), 2) if parts[i]]
        out: list[str] = []
        counts: dict[tuple, int] = {}
        pos, at = 0, 0  # word position, and next index into parts to copy
        while pos < len(words):
            i, lead, core, trail = words[pos]
            hit = None
            for key, canonical in self._index.get(norm(core), ()):
                n = len(key)
                if pos + n > len(words):
                    continue
                span = words[pos:pos + n]
                if tuple(norm(w[2]) for w in span) != key:
                    continue
                # a multi-token variant must not span punctuation between its tokens
                if any(span[k][3] or span[k + 1][1] for k in range(n - 1)):
                    continue
                original = " ".join(w[2] for w in span)
                if original == canonical:
                    continue  # already the canonical spelling
                hit = (n, canonical, original, span[-1][3])
                break
            if hit is None:
                pos += 1
                continue
            n, canonical, original, last_trail = hit
            out.append("".join(parts[at:i]))
            out.append(lead + canonical + last_trail)
            at = words[pos + n - 1][0] + 1
            counts[(original, canonical)] = counts.get((original, canonical), 0) + 1
            pos += n
        if not counts:
            return text, []
        out.append("".join(parts[at:]))
        corrections = [{"from": a, "to": b, "count": c} for (a, b), c in counts.items()]
        return "".join(out), corrections

    def prompt(self, max_chars: int = 600) -> str:
        """A short Urdu sentence plus the canonical terms in Latin letters, for Whisper's initial_prompt."""
        terms = [t for t in self.terms if any("a" <= c.lower() <= "z" for c in t) and not is_arabic_script(t)]
        text = URDU_INTRO
        for term in terms:
            extra = f" {term},"
            if len(text) + len(extra) > max_chars:
                break
            text += extra
        return text.rstrip(",")


def parse(text: str) -> Glossary:
    entries = []
    for raw in text.lstrip("﻿").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if "=" in line:
            canonical, _, rest = line.partition("=")
            canonical = canonical.strip()
            variants = [v.strip() for v in rest.split("|") if v.strip()]
        else:
            canonical, variants = line, []
        if canonical:
            entries.append((canonical, variants))
    return Glossary(entries)


def load(path) -> Glossary:
    try:
        return parse(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return Glossary()


def ensure_course_glossary(data_dir: Path, course_slug: str, starter: str) -> Path:
    """notes/<course-slug>/glossary.txt. Copied from glossaries/<starter>.txt once, never overwritten."""
    path = Path(data_dir) / "notes" / course_slug / "glossary.txt"
    if path.exists() or not starter:
        return path
    source = REPO_ROOT / "glossaries" / f"{starter}.txt"
    if source.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
    else:
        log.warning("starter glossary %s not found", source)
    return path


def apply_to_result(result: dict, glossary: Glossary) -> dict:
    """Fix every segment. Changed segments keep the old text in raw_text. No change leaves the result untouched."""
    merged: dict[tuple, int] = {}
    changed = False
    for seg in result.get("segments", []):
        new, corrections = glossary.apply(seg.get("text", ""))
        if new == seg.get("text", ""):
            continue
        changed = True
        seg.setdefault("raw_text", seg["text"])
        seg["text"] = new
        for c in corrections:
            merged[(c["from"], c["to"])] = merged.get((c["from"], c["to"]), 0) + c["count"]
    if not changed:
        return result
    for c in result.get("corrections") or []:  # keep what an earlier pass recorded
        merged[(c["from"], c["to"])] = merged.get((c["from"], c["to"]), 0) + c.get("count", 1)
    result["corrections"] = [{"from": a, "to": b, "count": n} for (a, b), n in merged.items()]
    result["text"] = " ".join(s["text"].strip() for s in result["segments"] if s["text"].strip())
    return result


# ---- suggestions -------------------------------------------------------------------------------------------

MAX_CANDIDATES = 150
EXAMPLE_CHARS = 80
SUGGEST_SYSTEM = """You help fix machine transcripts of Urdu lectures that mix in English.
English words and brand names are sometimes written phonetically in Urdu script.
You get a JSON list of frequent tokens, each with a count and a short example from the transcript.
Pick only the tokens that are clearly an English technical term, tool or brand name written in Urdu script,
and give its correct English spelling. Skip ordinary Urdu words and anything you are unsure about.
Reply with JSON only: a list of {"variant": "<token as given>", "canonical": "<English spelling>", "confidence": <0 to 1>}.
Reply with [] when none qualify."""


def candidates(result: dict, glossary: Glossary) -> list[dict]:
    """Frequent unknown Arabic-script tokens and 2-token sequences with one short example each."""
    known = glossary.variant_set() | {norm(t) for t in glossary.terms}
    counts: dict[str, int] = {}
    example: dict[str, str] = {}
    for seg in result.get("segments", []):
        text = seg.get("text", "")
        toks = [split_token(t)[1] for t in text.split()]
        toks = [t for t in toks if t]
        for n in (1, 2):
            for k in range(len(toks) - n + 1):
                gram = toks[k:k + n]
                if not all(is_arabic_script(t) for t in gram):
                    continue
                key = " ".join(clean(t) for t in gram)
                if len(key.replace(" ", "")) <= 2 or key in known:
                    continue
                if all(t in URDU_COMMON for t in gram):
                    continue
                counts[key] = counts.get(key, 0) + 1
                if key not in example:
                    at = text.find(gram[0])
                    example[key] = text[max(0, at - 20):max(0, at - 20) + EXAMPLE_CHARS].strip()
    ranked = sorted((k for k, c in counts.items() if c >= 2), key=lambda k: (-counts[k], k))[:MAX_CANDIDATES]
    return [{"token": k, "count": counts[k], "example": example[k]} for k in ranked]


def _json_list(text: str) -> list:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    return data if isinstance(data, list) else []


def suggest_variants(result: dict, glossary: Glossary, llm, course_dir=None) -> list:
    """Ask the notes AI which frequent tokens are English terms in Urdu script. Nothing is applied automatically.

    With course_dir the suggestions are merged into <course_dir>/glossary-suggestions.json.
    """
    found = candidates(result, glossary)
    if not found:
        return []
    by_token = {c["token"]: c for c in found}
    answer = llm.chat(SUGGEST_SYSTEM, json.dumps(found, ensure_ascii=False), max_tokens=4000, purpose="suggest")
    suggestions = []
    for item in _json_list(answer):
        if not isinstance(item, dict):
            continue
        variant = str(item.get("variant", "")).strip()
        canonical = str(item.get("canonical", "")).strip()
        if variant not in by_token or not canonical:
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        suggestions.append({"variant": variant, "canonical": canonical, "confidence": confidence,
                            "example": by_token[variant]["example"], "count": by_token[variant]["count"],
                            "status": "pending"})
    if course_dir is not None and suggestions:
        _merge_suggestions(Path(course_dir) / "glossary-suggestions.json", suggestions)
    return suggestions


def _merge_suggestions(path: Path, new: list) -> None:
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(old, list):
            old = []
    except (OSError, ValueError):
        old = []
    by_key = {(s.get("variant"), s.get("canonical")): s for s in old if isinstance(s, dict)}
    for s in new:
        key = (s["variant"], s["canonical"])
        if key in by_key:
            by_key[key]["count"] = by_key[key].get("count", 0) + s["count"]  # status of an earlier answer is kept
        else:
            by_key[key] = s
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
