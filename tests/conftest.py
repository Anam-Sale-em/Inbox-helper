"""Shared fixtures. No test here touches the network or needs API keys."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inbox import classify, draft, llm  # noqa: E402
from inbox.schema import Message  # noqa: E402

BASE_TIME = datetime(2026, 10, 1, 9, 0)


def make_message(
    msg_id: int,
    text: str = "Can we move my interview to next week?",
    name: str = "Asha Rao",
    email: str = "asha@example.com",
    minutes: int = 0,
) -> Message:
    return Message(
        id=msg_id,
        from_name=name,
        from_email=email,
        received_at=BASE_TIME + timedelta(minutes=minutes),
        text=text,
    )


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Reset stats and make any accidental real call fail loudly instead of hitting an API."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setattr(llm, "_sleep", lambda seconds: None)
    for module in (llm, classify, draft):
        module.reset_stats()
