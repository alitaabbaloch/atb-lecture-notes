import json
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest

import llm


def response(status):
    return httpx.Response(status, request=httpx.Request("POST", "http://invalid.test"))


def a_error(cls, status, message="x"):
    return cls(message, response=response(status), body=None)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: records the request, returns or raises what the test set."""
    calls: list = []
    outcome = None

    def __init__(self, *args, **kwargs):
        FakeAnthropic.init_args = (args, kwargs)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **kwargs):
        FakeAnthropic.calls.append(kwargs)
        outcome = FakeAnthropic.outcome
        if isinstance(outcome, Exception):
            raise outcome
        message = outcome if outcome is not None else self.message("answer")
        return Context(message)

    @staticmethod
    def message(text, stop="end_turn"):
        return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", thinking="x"),
                                                          SimpleNamespace(type="text", text=text)])


class Context:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return SimpleNamespace(get_final_message=lambda: self.message)

    def __exit__(self, *exc):
        return False


class FakeOpenAI:
    calls: list = []
    outcomes: dict = {}

    def __init__(self, *, api_key, base_url, timeout):
        self.base_url = base_url
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        FakeOpenAI.calls.append({"base_url": self.base_url, **kwargs})
        outcome = FakeOpenAI.outcomes.get(self.base_url, "deepseek answer")
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                                                        message=SimpleNamespace(content=outcome))])


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    for name in ("ATB_NOTES", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "NOTES_BASE_URL", "NOTES_API_KEY",
                 "NOTES_MODEL", "ATB_NOTES_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    llm.reset()
    llm.set_provider("auto")
    FakeAnthropic.calls, FakeAnthropic.outcome = [], None
    FakeOpenAI.calls, FakeOpenAI.outcomes = [], {}
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    yield
    llm.reset()
    llm.set_provider("auto")


def keys(monkeypatch, *names):
    values = {"anthropic": ("ANTHROPIC_API_KEY", "a-key"), "deepseek": ("DEEPSEEK_API_KEY", "d-key")}
    for n in names:
        if n == "custom":
            monkeypatch.setenv("NOTES_BASE_URL", "http://custom.test/v1")
            monkeypatch.setenv("NOTES_API_KEY", "c-key")
            monkeypatch.setenv("NOTES_MODEL", "custom-model")
        else:
            monkeypatch.setenv(*values[n])


def test_provider_order_and_availability(monkeypatch):
    assert llm.available() == []
    keys(monkeypatch, "custom", "deepseek", "anthropic")
    assert llm.available() == ["anthropic", "deepseek", "custom"]
    assert llm.chat("s", "u") == "answer"
    assert FakeOpenAI.calls == []


def test_placeholder_key_counts_as_unset(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-your-key-here")
    assert llm.available() == []


def test_deepseek_then_custom(monkeypatch):
    keys(monkeypatch, "deepseek", "custom")
    assert llm.chat("sys", "usr", max_tokens=123) == "deepseek answer"
    call = FakeOpenAI.calls[0]
    assert call["base_url"] == "https://api.deepseek.com" and call["model"] == "deepseek-chat"
    assert call["temperature"] == 0.2 and call["max_tokens"] == 123
    assert call["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    llm.chat("s", "u")
    assert FakeOpenAI.calls[1]["base_url"] == "http://custom.test/v1" and FakeOpenAI.calls[1]["model"] == "custom-model"


def test_forced_provider(monkeypatch):
    keys(monkeypatch, "anthropic", "deepseek")
    llm.set_provider("deepseek")
    assert llm.available() == ["deepseek"]
    assert llm.chat("s", "u") == "deepseek answer" and FakeAnthropic.calls == []
    monkeypatch.setenv("ATB_NOTES_PROVIDER", "anthropic")  # the env beats the config value
    assert llm.available() == ["anthropic"]


def test_forced_provider_without_a_key_is_an_error(monkeypatch):
    keys(monkeypatch, "deepseek")
    llm.set_provider("anthropic")
    with pytest.raises(RuntimeError, match="no notes AI key"):
        llm.chat("s", "u")


def test_notes_model_overrides_anthropic_only_when_forced(monkeypatch):
    keys(monkeypatch, "anthropic")
    monkeypatch.setenv("NOTES_MODEL", "claude-other")
    llm.chat("s", "u")
    assert FakeAnthropic.calls[0]["model"] == "claude-haiku-5-5"
    llm.set_provider("anthropic")
    llm.chat("s", "u")
    assert FakeAnthropic.calls[1]["model"] == "claude-other"


def test_fallback_after_auth_error_is_remembered(monkeypatch, caplog):
    keys(monkeypatch, "anthropic", "deepseek")
    FakeAnthropic.outcome = a_error(anthropic.AuthenticationError, 401)
    assert llm.chat("s", "u") == "deepseek answer"
    assert llm.available() == ["deepseek"]
    llm.chat("s", "u")
    assert len(FakeAnthropic.calls) == 1  # not tried again
    assert sum("unusable" in r.message for r in caplog.records) == 1


def test_fallback_after_permission_error(monkeypatch):
    keys(monkeypatch, "anthropic", "deepseek")
    FakeAnthropic.outcome = a_error(anthropic.PermissionDeniedError, 403)
    assert llm.chat("s", "u") == "deepseek answer"


def test_fallback_after_credit_balance_error(monkeypatch):
    keys(monkeypatch, "anthropic", "deepseek")
    FakeAnthropic.outcome = a_error(anthropic.BadRequestError, 400,
                                    "Your credit balance is too low to access the Anthropic API.")
    assert llm.chat("s", "u") == "deepseek answer"
    assert llm.available() == ["deepseek"]


def test_other_bad_request_is_not_a_fallback(monkeypatch):
    keys(monkeypatch, "anthropic", "deepseek")
    FakeAnthropic.outcome = a_error(anthropic.BadRequestError, 400, "messages: bad")
    with pytest.raises(RuntimeError, match="rejected the request"):
        llm.chat("s", "u")
    assert llm.available() == ["anthropic", "deepseek"] and FakeOpenAI.calls == []


def test_deepseek_balance_error_falls_to_custom(monkeypatch):
    keys(monkeypatch, "deepseek", "custom")
    FakeOpenAI.outcomes["https://api.deepseek.com"] = a_error(openai.APIStatusError, 402, "Insufficient Balance")
    assert llm.chat("s", "u") == "deepseek answer"  # the custom endpoint answered
    assert FakeOpenAI.calls[-1]["base_url"] == "http://custom.test/v1"


def test_other_errors_propagate(monkeypatch):
    keys(monkeypatch, "anthropic", "deepseek")
    FakeAnthropic.outcome = a_error(anthropic.RateLimitError, 429)
    with pytest.raises(RuntimeError, match="rate limit"):
        llm.chat("s", "u")
    assert FakeOpenAI.calls == []


def test_all_providers_unusable_is_a_clear_error(monkeypatch):
    keys(monkeypatch, "anthropic")
    FakeAnthropic.outcome = a_error(anthropic.AuthenticationError, 401)
    with pytest.raises(llm.NoProviderError, match="no notes AI is usable"):
        llm.chat("s", "u")
    with pytest.raises(llm.NoProviderError, match="no notes AI key"):
        llm.chat("s", "u")


def test_no_provider_available():
    with pytest.raises(RuntimeError, match="no notes AI key is set"):
        llm.chat("s", "u")


def test_verify_uses_the_next_provider(monkeypatch):
    keys(monkeypatch, "anthropic", "deepseek")
    assert llm.chat("s", "u", purpose="verify") == "deepseek answer"
    assert FakeAnthropic.calls == []
    assert llm.chat("s", "u", purpose="notes") == "answer"


def test_verify_with_one_provider_uses_it(monkeypatch):
    keys(monkeypatch, "anthropic")
    assert llm.chat("s", "u", purpose="verify") == "answer"


def test_anthropic_request_has_exactly_the_fixed_parameters(monkeypatch):
    keys(monkeypatch, "anthropic")
    llm.chat("the system", "the user", max_tokens=777)
    assert FakeAnthropic.init_args == ((), {})
    assert FakeAnthropic.calls == [{
        "model": "claude-haiku-5-5", "max_tokens": 32000, "system": "the system",  # 777 is raised to the floor
        "messages": [{"role": "user", "content": "the user"}],
        "output_config": {"effort": "high"},  # no fallback options: Haiku has no server-side fallback
    }]
    sent = json.dumps(FakeAnthropic.calls[0])
    for banned in ("temperature", "thinking", "top_p", "top_k", "tool_choice"):
        assert banned not in sent


def test_anthropic_refusal_fallback_only_for_models_that_have_one(monkeypatch):
    keys(monkeypatch, "anthropic")
    llm.set_provider("anthropic")
    monkeypatch.setenv("NOTES_MODEL", "claude-sonnet-5-5")
    llm.chat("s", "u")
    assert FakeAnthropic.calls[0]["model"] == "claude-sonnet-5-5"
    assert FakeAnthropic.calls[0]["betas"] == ["server-side-fallback-2026-07-01"]
    assert FakeAnthropic.calls[0]["fallbacks"] == "default"


def test_anthropic_only_text_blocks_are_read(monkeypatch):
    keys(monkeypatch, "anthropic")
    FakeAnthropic.outcome = SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="thinking", thinking="hidden"), SimpleNamespace(type="text", text="one "),
        SimpleNamespace(type="text", text="two")])
    assert llm.chat("s", "u") == "one two"


def test_anthropic_refusal_and_max_tokens(monkeypatch):
    keys(monkeypatch, "anthropic", "deepseek")
    FakeAnthropic.outcome = FakeAnthropic.message("partial", stop="max_tokens")
    with pytest.raises(RuntimeError, match="cut off"):
        llm.chat("s", "u")
    FakeAnthropic.outcome = SimpleNamespace(stop_reason="refusal", content=[])
    with pytest.raises(RuntimeError, match="declined"):
        llm.chat("s", "u")
    assert FakeOpenAI.calls == []


def test_only_two_files_import_the_sdks():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    for sdk, owner in (("anthropic", "llm_anthropic.py"), ("openai", "llm_openai.py")):
        users = sorted(p.name for p in [*(root / "server").glob("*.py"), *(root / "addons" / "notes").glob("*.py")]
                       if any(l.startswith((f"import {sdk}", f"from {sdk}")) for l in p.read_text().splitlines()))
        assert users == [owner]


def test_fake_notes_never_touch_a_provider(monkeypatch):
    monkeypatch.setenv("ATB_NOTES", "fake")
    keys(monkeypatch, "anthropic")
    assert llm.available() == ["fake"]
    assert llm.chat("s", "u", purpose="suggest") == "[]"
    assert FakeAnthropic.calls == []


def test_fake_section_verify_and_summary(monkeypatch):
    monkeypatch.setenv("ATB_NOTES", "fake")
    lines = ["[00:00] first line", "[00:05] second line", "[00:10] a student asks", "[00:15] [unclear until 00:20]",
             "[00:20] fourth INVENTED line", "[00:25] fifth", "[00:30] sixth", "[00:35] seventh INVENTED"]
    data = json.loads(llm.chat("s", "<lines>\n" + "\n".join(lines) + "\n</lines>", purpose="notes"))
    # real lines: 0 first, 1 second, 2 student, 3 fourth, 4 fifth, 5 sixth, 6 seventh; every third non-student: 0, 3, 6
    assert [n["t"] for n in data["notes"]] == ["00:00", "00:20", "00:35"]
    assert all(n["kind"] == "point" for n in data["notes"]) and data["unclear"] == []
    assert data["qa"] == [{"t": "00:10", "question": "a student asks", "answer_t": "00:20", "answer": "fourth INVENTED line"}]
    last = json.loads(llm.chat("s", "<lines>\n[00:00] a student speaks\n</lines>"))
    assert last["qa"][0]["answer"] == "" and last["qa"][0]["answer_t"] == "" and last["notes"] == []
    items = [{"id": 0, "type": "note", "text": "fine"}, {"id": 1, "type": "note", "text": "INVENTED thing"},
             {"id": 2, "type": "note", "text": "WRONGSPEAKER x"}, {"id": 3, "type": "qa", "question": "INVENTED?", "answer": "ok"},
             {"id": 4, "type": "qa", "question": "q", "answer": "INVENTED"}]
    verdicts = json.loads(llm.chat("s", f"<items>{json.dumps(items)}</items>", purpose="verify"))
    assert [v["verdict"] for v in verdicts] == ["supported", "unsupported", "wrong_speaker", "supported", "unsupported"]
    assert llm.chat("s", "u", purpose="summary") == llm.chat("s", "other", purpose="summary")
