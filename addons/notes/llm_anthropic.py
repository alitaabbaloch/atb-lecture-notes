"""Claude through the official Anthropic SDK. The only file that imports `anthropic`."""
import logging

import anthropic

from llm import ProviderUnusable

log = logging.getLogger("atb")

MODEL = "claude-haiku-5-5"
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}  # not sent for Haiku
MIN_MAX_TOKENS = 32000  # thinking counts toward max_tokens on this model; a low cap cuts answers off
EFFORT = "high"  # on a real lesson "high" gave fuller notes than "medium" at the same accuracy, about 3x slower


def chat(system: str, user: str, max_tokens: int, model: str | None = None) -> str:
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    try:
        # No temperature, top_p, top_k or thinking: this model rejects sampling parameters and thinks adaptively.
        with client.beta.messages.stream(
            model=model or MODEL,
            max_tokens=max(max_tokens, MIN_MAX_TOKENS),
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": EFFORT},
            **({} if "haiku" in (model or MODEL) else FALLBACK),  # Haiku has no server-side fallback
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ProviderUnusable("anthropic", "the API key was rejected") from e
    except anthropic.PermissionDeniedError as e:
        raise ProviderUnusable("anthropic", "the API key has no permission for this request") from e
    except anthropic.BadRequestError as e:
        if "credit balance" in str(getattr(e, "message", e)).lower():
            raise ProviderUnusable("anthropic", "the credit balance is too low") from e
        raise RuntimeError(f"Anthropic rejected the request: {getattr(e, 'message', e)}") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Anthropic rate limit reached, try again later") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic error {e.status_code}: {getattr(e, 'message', e)}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("could not reach Anthropic, check the internet connection") from e

    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined to answer this request")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens reached)")
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if not text:
        raise RuntimeError("empty response from Claude")
    return text
