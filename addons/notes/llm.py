"""Provider-neutral chat for the notes steps. No SDK is imported here; see llm_anthropic.py and llm_openai.py.

Order when the provider is "auto": anthropic (ANTHROPIC_API_KEY), deepseek (DEEPSEEK_API_KEY),
custom (NOTES_BASE_URL + NOTES_API_KEY + NOTES_MODEL). ATB_NOTES=fake uses a built-in fake with no network.
"""
import json
import logging
import os
import re
import threading

log = logging.getLogger("atb")

ORDER = ("anthropic", "deepseek", "custom")
_setting = "auto"
_unusable: dict[str, str] = {}
_lock = threading.Lock()


class ProviderUnusable(Exception):
    """The key was rejected, has no permission, or the account has no credit. The provider is skipped from now on."""

    def __init__(self, provider: str, reason: str):
        super().__init__(f"{provider}: {reason}")
        self.provider, self.reason = provider, reason


class NoProviderError(RuntimeError):
    pass


def set_provider(name: str | None) -> None:
    """The config value notes.provider: "auto" or a provider name. The env ATB_NOTES_PROVIDER wins."""
    global _setting
    _setting = (name or "auto").strip().lower()


def reset() -> None:
    """Forget which providers were marked unusable (tests)."""
    with _lock:
        _unusable.clear()


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    return "" if value.startswith("sk-your") else value


def _configured(name: str) -> bool:
    if name == "anthropic":
        return bool(_env("ANTHROPIC_API_KEY"))
    if name == "deepseek":
        return bool(_env("DEEPSEEK_API_KEY"))
    if name == "custom":
        return all(_env(k) for k in ("NOTES_BASE_URL", "NOTES_API_KEY", "NOTES_MODEL"))
    return False


def forced() -> str | None:
    value = (os.environ.get("ATB_NOTES_PROVIDER") or _setting or "auto").strip().lower()
    return value if value in ORDER else None


def is_fake() -> bool:
    return os.environ.get("ATB_NOTES") == "fake"


def available() -> list[str]:
    """Usable providers in the order they will be tried."""
    if is_fake():
        return ["fake"]
    names = [forced()] if forced() else list(ORDER)
    with _lock:
        return [n for n in names if _configured(n) and n not in _unusable]


def _call(name: str, system: str, user: str, max_tokens: int) -> str:
    if name == "anthropic":
        import llm_anthropic
        return llm_anthropic.chat(system, user, max_tokens, model=_env("NOTES_MODEL") if forced() == "anthropic" else None)
    import llm_openai
    if name == "deepseek":
        # NOTES_MODEL belongs to the custom endpoint when one is configured, unless deepseek is forced
        own = forced() == "deepseek" or not _configured("custom")
        return llm_openai.chat(name, system, user, max_tokens, api_key=_env("DEEPSEEK_API_KEY"),
                               base_url=llm_openai.DEEPSEEK_URL,
                               model=(_env("NOTES_MODEL") if own else "") or llm_openai.DEEPSEEK_MODEL)
    return llm_openai.chat(name, system, user, max_tokens, api_key=_env("NOTES_API_KEY"),
                           base_url=_env("NOTES_BASE_URL"), model=_env("NOTES_MODEL"))


def chat(system: str, user: str, *, max_tokens: int = 8000, purpose: str = "notes") -> str:
    """One model call. purpose is "notes", "summary", "suggest", "roman" or "verify"; verify prefers a second provider."""
    if is_fake():
        return _fake(system, user, purpose)
    order = available()
    if purpose == "verify" and len(order) > 1:
        order = order[1:] + order[:1]  # an independent second opinion first
    if not order:
        raise NoProviderError(
            "no notes AI key is set. Add ANTHROPIC_API_KEY or DEEPSEEK_API_KEY to secrets.env in the app folder "
            "(or NOTES_BASE_URL, NOTES_API_KEY and NOTES_MODEL for a custom endpoint) and restart the server.")
    last = None
    for name in order:
        with _lock:
            if name in _unusable:
                continue
        try:
            return _call(name, system, user, max_tokens)
        except ProviderUnusable as e:
            with _lock:
                first = name not in _unusable
                _unusable[name] = e.reason
            if first:
                log.warning("notes provider %s is unusable for this run (%s)", name, e.reason)
            last = e
    raise NoProviderError(f"no notes AI is usable ({last}). Fix the key or credit balance and restart the server.")


# ---- the fake (ATB_NOTES=fake) -----------------------------------------------------------------------------

_LINE = re.compile(r"^\[(\d+(?::\d\d){1,2})\] (.*)$", re.M)


_ARABIC_WORD = re.compile("[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")


def _fake_roman(user: str) -> str:
    """Test switches: ATB_FAKE_ROMAN=short (one item too few), arabic (input unchanged), fail (raises)."""
    mode = os.environ.get("ATB_FAKE_ROMAN", "")
    if mode == "fail":
        raise RuntimeError("fake roman failure")
    lines = json.loads(user)
    if mode == "arabic":
        return json.dumps(lines, ensure_ascii=False)
    out = [" ".join("urdu" if _ARABIC_WORD.search(w) else w for w in line.split()) for line in lines]
    return json.dumps(out[:-1] if mode == "short" else out, ensure_ascii=False)


def _fake(system: str, user: str, purpose: str) -> str:
    if purpose == "roman":
        return _fake_roman(user)
    if purpose == "suggest":
        return "[]"
    if purpose == "summary":
        return "This lesson was summarised by the built-in fake notes engine."
    if purpose == "verify":
        found = re.search(r"<items>\s*(.*?)\s*</items>", user, re.S)
        verdicts = []
        for item in json.loads(found.group(1)) if found else []:
            if item.get("type") == "qa":
                bad = "INVENTED" in item.get("answer", "")
                verdict = "unsupported" if bad else "supported"
            else:
                text = item.get("text", "")
                verdict = "unsupported" if "INVENTED" in text else "wrong_speaker" if "WRONGSPEAKER" in text else "supported"
            verdicts.append({"id": item["id"], "verdict": verdict})
        return json.dumps(verdicts)
    body = re.search(r"<lines>\s*(.*?)\s*</lines>", user, re.S)
    lines = [(t, text.strip()) for t, text in _LINE.findall(body.group(1) if body else user)
             if "[unclear until" not in text]
    notes, qa = [], []
    for i, (t, text) in enumerate(lines):
        if "student" in text.lower():
            nxt = lines[i + 1] if i + 1 < len(lines) else ("", "")
            qa.append({"t": t, "question": text, "answer_t": nxt[0], "answer": nxt[1]})
        elif i % 3 == 0:
            notes.append({"t": t, "kind": "point", "text": text})
    return json.dumps({"summary": f"Fake summary of {len(lines)} lines.", "notes": notes, "qa": qa, "unclear": []})
