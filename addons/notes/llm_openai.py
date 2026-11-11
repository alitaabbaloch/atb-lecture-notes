"""DeepSeek and any custom OpenAI-compatible endpoint through the `openai` package. The only file that imports it."""
import logging

import openai

from llm import ProviderUnusable

log = logging.getLogger("atb")

DEEPSEEK_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"


def chat(name: str, system: str, user: str, max_tokens: int, *, api_key: str, base_url: str, model: str) -> str:
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=600)
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except openai.AuthenticationError as e:
        raise ProviderUnusable(name, "the API key was rejected") from e
    except openai.PermissionDeniedError as e:
        raise ProviderUnusable(name, "the API key has no permission for this request") from e
    except openai.APIStatusError as e:
        if e.status_code == 402:  # DeepSeek: insufficient balance
            raise ProviderUnusable(name, "the account balance is too low") from e
        raise RuntimeError(f"{name} error {e.status_code}: {getattr(e, 'message', e)}") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(f"could not reach {name}, check the internet connection") from e
    choice = r.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError(f"{name} answer was cut off (max_tokens reached)")
    text = (choice.message.content or "").strip()
    if not text:
        raise RuntimeError(f"empty response from {name}")
    return text
