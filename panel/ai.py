"""Draft society emails with the configured AI engine.

The engine, API key and tone-of-voice all live on ``SiteConfig`` (set by the
super admin — see core/models.py). Five providers are supported; DeepSeek is
the default. Every call is a plain HTTPS request via the standard library, so
the SRCF deployment needs **no extra packages or vendor SDKs** — keeping the
install small and the dependency surface tiny (see DESIGN.md).

The public entry point is :func:`draft_email`. It raises :class:`AIDraftError`
on any failure, with a short message that is safe to show an admin (it never
contains the API key).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# How long to wait on the provider before giving up. Gunicorn runs only a
# couple of workers on SRCF, so a hung request must not tie one up for long.
TIMEOUT_SECONDS = 30

# Cap the drafted body — a "What's On" digest is short; this bounds cost.
MAX_OUTPUT_TOKENS = 2000

# Per-engine wiring: (protocol, endpoint, default model). The model can be
# overridden per engine without a code change via an environment variable,
# e.g. MSS_AI_MODEL_ANTHROPIC=claude-haiku-4-5 for a cheaper draft.
_ENGINES = {
    "deepseek": ("openai", "https://api.deepseek.com/v1/chat/completions", "deepseek-chat"),
    "openai": ("openai", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "mistral": ("openai", "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest"),
    "anthropic": ("anthropic", "https://api.anthropic.com/v1/messages", "claude-opus-4-8"),
    "gemini": ("gemini", "https://generativelanguage.googleapis.com/v1beta/models", "gemini-1.5-flash"),
}


class AIDraftError(Exception):
    """A drafting attempt failed. The message is safe to show an admin."""


def draft_email(engine, api_key, system_prompt, user_prompt, *, timeout=TIMEOUT_SECONDS):
    """Ask ``engine`` to produce an email body and return it as a string.

    ``engine`` is a ``SiteConfig.email_ai_engine`` value. Raises
    :class:`AIDraftError` if the engine is unknown, the key is missing, the
    network/provider fails, or the response can't be parsed.
    """
    if engine not in _ENGINES:
        raise AIDraftError(f"Unknown AI engine {engine!r}.")
    if not (api_key or "").strip():
        raise AIDraftError("No API key is set for the AI engine.")

    protocol, endpoint, default_model = _ENGINES[engine]
    model = os.environ.get(f"MSS_AI_MODEL_{engine.upper()}", default_model)

    if protocol == "openai":
        return _draft_openai(endpoint, model, api_key, system_prompt, user_prompt, timeout)
    if protocol == "anthropic":
        return _draft_anthropic(endpoint, model, api_key, system_prompt, user_prompt, timeout)
    if protocol == "gemini":
        return _draft_gemini(endpoint, model, api_key, system_prompt, user_prompt, timeout)
    raise AIDraftError(f"Unsupported protocol {protocol!r}.")  # unreachable


# --- provider calls ----------------------------------------------------------

def _draft_openai(endpoint, model, api_key, system_prompt, user_prompt, timeout):
    """DeepSeek / OpenAI / Mistral — the OpenAI chat-completions shape."""
    payload = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.6,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    data = _post(endpoint, payload, {"Authorization": f"Bearer {api_key}"}, timeout)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIDraftError("The AI engine returned an unexpected response.")


def _draft_anthropic(endpoint, model, api_key, system_prompt, user_prompt, timeout):
    """Anthropic (Claude) — the Messages API.

    Note: current Claude models (e.g. claude-opus-4-8) reject sampling
    parameters such as ``temperature``, so none is sent.
    """
    payload = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    data = _post(endpoint, payload, headers, timeout)
    try:
        # content is a list of blocks; take the first text block.
        for block in data["content"]:
            if block.get("type") == "text":
                return block["text"].strip()
        raise AIDraftError("The AI engine returned no text.")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIDraftError("The AI engine returned an unexpected response.")


def _draft_gemini(base, model, api_key, system_prompt, user_prompt, timeout):
    """Google Gemini — generateContent. The key goes in the query string."""
    endpoint = f"{base}/{model}:generateContent?key={urllib.parse.quote(api_key)}"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS, "temperature": 0.6},
    }
    data = _post(endpoint, payload, {}, timeout)
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIDraftError("The AI engine returned an unexpected response.")


# --- HTTP plumbing -----------------------------------------------------------

def _post(url, payload, headers, timeout):
    """POST ``payload`` as JSON and return the parsed JSON response.

    Raises :class:`AIDraftError` (never leaking the key) on any HTTP, network,
    timeout or JSON error.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AIDraftError(_http_error_message(exc))
    except urllib.error.URLError as exc:
        raise AIDraftError(f"Could not reach the AI engine ({exc.reason}).")
    except TimeoutError:
        raise AIDraftError("The AI engine timed out.")
    except (ValueError, json.JSONDecodeError):
        raise AIDraftError("The AI engine returned malformed data.")


def _http_error_message(exc):
    """Turn a provider error response into a short, key-free admin message."""
    detail = ""
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        # OpenAI-style {"error": {"message": ...}} or Anthropic
        # {"error": {"message": ...}} / Gemini {"error": {"message": ...}}.
        err = payload.get("error")
        if isinstance(err, dict):
            detail = err.get("message", "")
        elif isinstance(err, str):
            detail = err
    except Exception:
        detail = ""
    if exc.code in (401, 403):
        return "The AI engine rejected the API key (check it on the Super admin tab)."
    if exc.code == 429:
        return "The AI engine is rate-limited or out of quota — try again shortly."
    return f"The AI engine returned an error ({exc.code}). {detail}".strip()
