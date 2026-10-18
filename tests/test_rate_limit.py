"""Day 5 live run: Gemini free tier allows 5 requests/minute. 3 parallel calls hit 429 within a
minute, each burned 3 retries (2/4/8 s) before falling back. Fix: pace calls per provider, and
when the server asks for a long wait, switch provider instead of retrying."""

import pytest

from inbox import llm


class Clock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def sleep(self, seconds):
        self.slept.append(round(seconds, 2))
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(llm, "_now", lambda: c.now)
    monkeypatch.setattr(llm, "_sleep", c.sleep)
    llm._reset_pacing()
    return c


def test_sixth_gemini_call_in_a_minute_waits(monkeypatch, clock):
    monkeypatch.setenv("GEMINI_RPM", "5")
    for _ in range(5):
        llm._pace("gemini")
    assert clock.slept == []
    llm._pace("gemini")
    assert clock.slept == [60.0]  # waits until the first call is a minute old


def test_providers_are_paced_separately(monkeypatch, clock):
    monkeypatch.setenv("GEMINI_RPM", "1")
    monkeypatch.setenv("GROQ_RPM", "30")
    llm._pace("gemini")
    llm._pace("groq")
    assert clock.slept == []


def test_zero_turns_pacing_off(monkeypatch, clock):
    monkeypatch.setenv("GEMINI_RPM", "0")
    for _ in range(50):
        llm._pace("gemini")
    assert clock.slept == []


class Quota(Exception):
    code = 429


def test_long_server_wait_switches_provider_without_retrying(monkeypatch, clock):
    calls = []

    def gemini(prompt, system):
        calls.append("gemini")
        raise Quota("429 RESOURCE_EXHAUSTED ... 'retryDelay': '19s'")

    def groq(prompt, system):
        calls.append("groq")
        return '{"items": []}', {}

    monkeypatch.setitem(llm._PROVIDERS, "gemini", gemini)
    monkeypatch.setitem(llm._PROVIDERS, "groq", groq)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("RECORD", raising=False)
    assert llm.call_llm("hi") == '{"items": []}'
    assert calls == ["gemini", "groq"]          # no 2/4/8 s retries on gemini first
    assert 2 not in clock.slept and 4 not in clock.slept


def test_short_server_wait_still_retries(monkeypatch, clock):
    attempts = []

    def gemini(prompt, system):
        attempts.append(1)
        if len(attempts) == 1:
            raise Quota("429 ... Please retry in 1.5s.")
        return '{"items": []}', {}

    monkeypatch.setitem(llm._PROVIDERS, "gemini", gemini)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("RECORD", raising=False)
    assert llm.call_llm("hi") == '{"items": []}' and len(attempts) == 2


@pytest.mark.parametrize("text,seconds", [
    ("{'retryDelay': '19s'}", 19.0), ("Please retry in 18.95s.", 18.95),
    ("Please retry in 889.2ms.", 0.89), ("no hint here", None),
])
def test_server_retry_hint_is_read(text, seconds):
    got = llm._server_wait(Exception(text))
    assert got == (None if seconds is None else pytest.approx(seconds, abs=0.01))
