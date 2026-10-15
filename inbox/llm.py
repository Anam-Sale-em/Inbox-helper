"""The only module that talks to an LLM: provider choice, retries, fallback, record/replay."""

import hashlib
import os
import sys
import time
from functools import cache
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

load_dotenv()

MOCK_DIR = Path(__file__).resolve().parent.parent / "data" / "mock_responses"
BACKOFF_SECONDS = (2, 4, 8)
REAL_PROVIDERS = ("gemini", "groq")

# Swappable so tests can skip real waiting: monkeypatch llm._sleep.
_sleep: Callable[[float], None] = time.sleep


class LLMUnavailable(Exception):
    """Both real providers failed after retries and fallback."""


class MockResponseMissing(LookupError):
    """Mock mode asked for a response that was never recorded."""


_stats: dict[str, Any] = {"retries": 0, "fallbacks": 0, "provider_used": {}}


def get_stats() -> dict[str, Any]:
    return {
        "retries": _stats["retries"],
        "fallbacks": _stats["fallbacks"],
        "provider_used": dict(_stats["provider_used"]),
    }


def reset_stats() -> None:
    _stats["retries"] = 0
    _stats["fallbacks"] = 0
    _stats["provider_used"] = {}


def call_llm(prompt: str, system: str | None = None) -> str:
    """Return raw JSON text from the configured provider."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    key = _cache_key(prompt, system)

    if provider == "mock":
        text = _load_mock(key)
        _mark_used("mock")
        return text

    if provider not in REAL_PROVIDERS:
        raise ValueError(
            f"LLM_PROVIDER must be gemini, groq or mock, got {provider!r}"
        )

    errors: list[str] = []
    for position, name in enumerate((provider, _other(provider))):
        if position > 0:
            _stats["fallbacks"] += 1
            _log(f"falling back to {name}")
        try:
            text = _call_with_retry(name, prompt, system)
        except Exception as exc:  # any provider failure moves us to the next one
            errors.append(f"{name}: {exc}")
            _log(f"{name} failed: {exc}")
            continue

        _mark_used(name)
        if os.getenv("RECORD") == "1":
            _save_mock(key, text)
        return text

    raise LLMUnavailable("Both providers failed. " + " | ".join(errors))


# --- retry -----------------------------------------------------------------


def _call_with_retry(name: str, prompt: str, system: str | None) -> str:
    call = _PROVIDERS[name]
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        try:
            return call(prompt, system)
        except Exception as exc:
            if attempt == len(BACKOFF_SECONDS) or not _is_retryable(exc):
                raise
            delay = BACKOFF_SECONDS[attempt]
            _stats["retries"] += 1
            _log(f"{name} returned HTTP {_status_code(exc)}, retrying in {delay}s")
            _sleep(delay)
    raise AssertionError("unreachable")


def _status_code(exc: Exception) -> int | None:
    # groq errors use .status_code, google-genai errors use .code
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _is_retryable(exc: Exception) -> bool:
    code = _status_code(exc)
    return code is not None and (code == 429 or 500 <= code <= 599)


# --- providers -------------------------------------------------------------


def _call_gemini(prompt: str, system: str | None) -> str:
    from google.genai import types

    response = _gemini_client().models.generate_content(
        model=_require_env("GEMINI_MODEL"),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
        ),
    )
    return _non_empty(response.text, "gemini")


def _call_groq(prompt: str, system: str | None) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = _groq_client().chat.completions.create(
        model=_require_env("GROQ_MODEL"),
        messages=messages,
        response_format={"type": "json_object"},
    )
    return _non_empty(response.choices[0].message.content, "groq")


@cache
def _gemini_client() -> Any:
    from google import genai

    return genai.Client()  # reads GEMINI_API_KEY


@cache
def _groq_client() -> Any:
    from groq import Groq

    # SDK retries are off so only our 2s/4s/8s backoff applies.
    return Groq(max_retries=0)  # reads GROQ_API_KEY


_PROVIDERS: dict[str, Callable[[str, str | None], str]] = {
    "gemini": _call_gemini,
    "groq": _call_groq,
}


# --- record / replay -------------------------------------------------------


def _cache_key(prompt: str, system: str | None) -> str:
    # Separator stops ("ab", "c") and ("a", "bc") from hashing the same.
    raw = f"{system or ''}\x00{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _save_mock(key: str, text: str) -> None:
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    (MOCK_DIR / f"{key}.json").write_text(text, encoding="utf-8")


def _load_mock(key: str) -> str:
    path = MOCK_DIR / f"{key}.json"
    if not path.exists():
        raise MockResponseMissing(
            f"No recorded response for hash {key} (expected {path}). "
            "Run once with RECORD=1 and LLM_PROVIDER=gemini or groq to record it."
        )
    return path.read_text(encoding="utf-8")


# --- helpers ---------------------------------------------------------------


def _other(provider: str) -> str:
    return "groq" if provider == "gemini" else "gemini"


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set in .env")
    return value


def _non_empty(text: str | None, provider: str) -> str:
    if not text:
        raise RuntimeError(f"{provider} returned an empty response")
    return text


def _mark_used(provider: str) -> None:
    used = _stats["provider_used"]
    used[provider] = used.get(provider, 0) + 1
    _log(f"answered by {provider}")


def _log(message: str) -> None:
    print(f"[llm] {message}", file=sys.stderr)
