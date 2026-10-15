"""Classify all messages into the 8 intents with a single LLM call (plus validation retries)."""

import json
import sys
from typing import Any

from pydantic import ValidationError

from inbox.llm import LLMUnavailable, call_llm
from inbox.schema import Classification, ClassificationResponse, Message, check_ids

MAX_RETRIES = 2

SYSTEM_PROMPT = """You classify messages that job candidates sent to a recruiter.

Intents:
- reschedule_interview: wants to move, cancel or change an interview time
- application_status: asks where their application stands or what happens next
- salary_question: asks about pay, CTC, salary range or benefits
- offer_response: accepts, declines, negotiates or asks about an offer they received
- withdrawal: wants to withdraw from the hiring process
- document_submission: is sending, or asking about, documents like a resume, ID or certificates
- other: fits none of the intents above
- needs_human: sensitive, a complaint, legal, or too unclear to classify safely

Return JSON only, in exactly this shape:
{"items": [{"id": <int>, "intent": "<one of the intents above>", "confidence": <float from 0 to 1>}]}

Return exactly one item for every input id: no missing, extra or repeated ids."""

_stats: dict[str, Any] = {"retries": 0}


def get_stats() -> dict[str, Any]:
    return dict(_stats)


def reset_stats() -> None:
    _stats["retries"] = 0


def classify(messages: list[Message]) -> dict[int, Classification]:
    """Return {message_id: Classification} for every input message. Never raises on LLM failure."""
    if not messages:
        return {}

    expected = {m.id for m in messages}
    base_prompt = _build_prompt(messages)
    results: dict[int, Classification] = {}
    error: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            _stats["retries"] += 1
            _log(f"retry {attempt}/{MAX_RETRIES}")

        prompt = base_prompt if error is None else base_prompt + _retry_note(error)
        try:
            raw = call_llm(prompt, system=SYSTEM_PROMPT)
        except LLMUnavailable as exc:
            _log(f"LLM unavailable, unclassified messages go to needs_human: {exc}")
            break

        try:
            response = ClassificationResponse.model_validate_json(raw)
        except ValidationError as exc:
            error = str(exc)
            continue

        returned_ids = [item.id for item in response.items]
        _merge(results, response.items, expected, returned_ids)

        id_errors = check_ids(expected, returned_ids)
        if not id_errors:
            break
        error = "\n".join(id_errors)

    missing = expected - results.keys()
    if missing:
        _log(f"{len(missing)} message(s) marked needs_human: {sorted(missing)}")
    for msg_id in missing:
        results[msg_id] = Classification(id=msg_id, intent="needs_human", confidence=0.0)

    return {m.id: results[m.id] for m in messages}


def _build_prompt(messages: list[Message]) -> str:
    payload = [{"id": m.id, "text": m.text} for m in messages]
    return "Classify these messages:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _retry_note(error: str) -> str:
    return (
        "\n\nYour previous response was rejected with this error:\n"
        f"{error}\n"
        "Return the full corrected JSON with one item for every input id."
    )


def _merge(
    results: dict[int, Classification],
    items: list[Classification],
    expected: set[int],
    returned_ids: list[int],
) -> None:
    """Keep items for expected ids that appeared exactly once; later attempts overwrite earlier ones."""
    for item in items:
        if item.id in expected and returned_ids.count(item.id) == 1:
            results[item.id] = item


def _log(message: str) -> None:
    print(f"[classify] {message}", file=sys.stderr)
