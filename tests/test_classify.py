import json

from inbox import classify as classify_module
from inbox.classify import classify
from tests.conftest import make_message

INVALID_JSON = '{"items": [ this is not json'


def valid_response(ids: list[int], intent: str = "reschedule_interview") -> str:
    return json.dumps(
        {"items": [{"id": i, "intent": intent, "confidence": 0.9} for i in ids]}
    )


def fake_llm(monkeypatch, responses: list[str]) -> list[str]:
    """Replace call_llm with a queue of canned responses; return the list of prompts it saw."""
    prompts: list[str] = []
    queue = iter(responses)

    def fake(prompt: str, system: str | None = None) -> str:
        prompts.append(prompt)
        return next(queue)

    monkeypatch.setattr(classify_module, "call_llm", fake)
    return prompts


def test_classify_retry_then_success(monkeypatch):
    messages = [make_message(1), make_message(2, email="ravi@example.com")]
    prompts = fake_llm(monkeypatch, [INVALID_JSON, valid_response([1, 2])])

    result = classify(messages)

    assert {r.intent for r in result.values()} == {"reschedule_interview"}
    assert classify_module.get_stats()["retries"] == 1
    assert len(prompts) == 2
    assert "rejected with this error" in prompts[1]


def test_classify_exhausted_marks_everything_needs_human(monkeypatch):
    messages = [make_message(1), make_message(2, email="ravi@example.com")]
    fake_llm(monkeypatch, [INVALID_JSON] * 3)

    result = classify(messages)

    assert set(result) == {1, 2}
    assert all(r.intent == "needs_human" and r.confidence == 0.0 for r in result.values())
    assert classify_module.get_stats()["retries"] == 2


def test_missing_id_ends_as_needs_human(monkeypatch):
    messages = [make_message(1), make_message(2, email="ravi@example.com")]
    prompts = fake_llm(monkeypatch, [valid_response([1])] * 3)

    result = classify(messages)

    assert result[1].intent == "reschedule_interview"
    assert result[2].intent == "needs_human"
    assert result[2].confidence == 0.0
    assert "Missing ids" in prompts[1]
