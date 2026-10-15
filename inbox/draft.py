"""Draft recruiter replies in batches of 10, one LLM call per batch (plus validation retries)."""

import json
import sys
from typing import Any

from pydantic import ValidationError

from inbox.llm import LLMUnavailable, call_llm
from inbox.schema import Classification, Draft, DraftResponse, Message, check_ids

BATCH_SIZE = 10
MAX_RETRIES = 2

SYSTEM_PROMPT = """You draft replies from a recruiter to messages sent by job candidates.

Rules for every reply:
- Under 80 words.
- Polite and professional.
- Address the candidate by their first name only.
- Never promise salary, dates, offers or results of any kind.
- Never mention other candidates or share anything about them.

Each input has an id, the sender's name, the message's intent and the message text.

Return JSON only, in exactly this shape:
{"items": [{"id": <int>, "reply": "<reply text>"}]}

Return exactly one item for every input id: no missing, extra or repeated ids."""

# Revisions have their own rules: the recruiter is the human in charge, so their
# feedback can add dates, times or salary. The no-promise rule above only guards
# first drafts, where the AI must not invent commitments from a candidate's message.
REVISE_SYSTEM_PROMPT = """You revise a reply from a recruiter to a job candidate, based on the recruiter's feedback.

The recruiter's feedback is a decision by the human in charge. Follow it exactly,
including any specific dates, times or salary figures the recruiter asks you to include.

Rules for the revised reply:
- Under 80 words.
- Polite and professional.
- Address the candidate by their first name only.
- Never mention other candidates or share anything about them, even if the feedback asks.
  In that case, keep the current reply and add nothing about other candidates.

The input has the message id, the sender's name, the intent, the candidate's message,
the current reply and the recruiter's feedback.

Return JSON only, in exactly this shape:
{"items": [{"id": <int>, "reply": "<revised reply text>"}]}

Return exactly one item, for the input id."""

_stats: dict[str, Any] = {"retries": 0, "batches": 0, "empty_drafts": 0}


def get_stats() -> dict[str, Any]:
    return dict(_stats)


def reset_stats() -> None:
    for key in _stats:
        _stats[key] = 0


def draft_replies(
    messages: list[Message], classifications: dict[int, Classification]
) -> dict[int, Draft]:
    """Return {message_id: Draft} for every input message. Empty reply = handle manually."""
    drafts: dict[int, Draft] = {}
    for start in range(0, len(messages), BATCH_SIZE):
        batch = messages[start : start + BATCH_SIZE]
        drafts.update(_draft_batch(batch, classifications))
    return {m.id: drafts[m.id] for m in messages}


def _draft_batch(
    batch: list[Message], classifications: dict[int, Classification]
) -> dict[int, Draft]:
    _stats["batches"] += 1
    expected = {m.id for m in batch}
    base_prompt = _build_prompt(batch, classifications)
    results: dict[int, Draft] = {}
    error: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            _stats["retries"] += 1
            _log(f"batch {_stats['batches']}: retry {attempt}/{MAX_RETRIES}")

        prompt = base_prompt if error is None else base_prompt + _retry_note(error)
        try:
            raw = call_llm(prompt, system=SYSTEM_PROMPT)
        except LLMUnavailable as exc:
            _log(f"batch {_stats['batches']}: LLM unavailable, leaving drafts empty: {exc}")
            break

        try:
            response = DraftResponse.model_validate_json(raw)
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
        _stats["empty_drafts"] += len(missing)
        _log(f"no draft for {sorted(missing)}, handle manually")
    for msg_id in missing:
        results[msg_id] = Draft(id=msg_id, reply="")

    return results


def revise_reply(message: Message, intent: str, current_reply: str, feedback: str) -> Draft:
    """Rewrite one reply from recruiter feedback. On any failure, return current_reply unchanged."""
    payload = {
        "id": message.id,
        "from_name": message.from_name,
        "intent": intent,
        "text": message.text,
        "current_reply": current_reply,
        "recruiter_feedback": feedback,
    }
    base_prompt = "Revise the reply for this message:\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )
    error: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            _stats["retries"] += 1
            _log(f"revise #{message.id}: retry {attempt}/{MAX_RETRIES}")

        prompt = base_prompt if error is None else base_prompt + _retry_note(error)
        try:
            raw = call_llm(prompt, system=REVISE_SYSTEM_PROMPT)
        except LLMUnavailable as exc:
            _log(f"revise #{message.id}: LLM unavailable, keeping current reply: {exc}")
            break
        except Exception as exc:  # e.g. missing mock recording; revising must never crash the UI
            _log(f"revise #{message.id}: call failed, keeping current reply: {exc}")
            break

        try:
            response = DraftResponse.model_validate_json(raw)
        except ValidationError as exc:
            error = str(exc)
            continue

        id_errors = check_ids({message.id}, [item.id for item in response.items])
        if not id_errors and response.items[0].reply.strip():
            return response.items[0]
        error = "\n".join(id_errors) or "The reply is empty. Return a non-empty reply."
    else:
        _log(f"revise #{message.id}: still invalid after {MAX_RETRIES} retries, keeping current reply")

    # model_construct: the current reply may be a manual edit over 80 words; don't re-validate it.
    return Draft.model_construct(id=message.id, reply=current_reply)


def _build_prompt(
    batch: list[Message], classifications: dict[int, Classification]
) -> str:
    payload = [
        {
            "id": m.id,
            "from_name": m.from_name,
            "intent": _intent_for(m.id, classifications),
            "text": m.text,
        }
        for m in batch
    ]
    return "Draft a reply for each of these messages:\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def _intent_for(msg_id: int, classifications: dict[int, Classification]) -> str:
    found = classifications.get(msg_id)
    return found.intent if found else "needs_human"


def _retry_note(error: str) -> str:
    return (
        "\n\nYour previous response was rejected with this error:\n"
        f"{error}\n"
        "Return the full corrected JSON with one item for every input id."
    )


def _merge(
    results: dict[int, Draft],
    items: list[Draft],
    expected: set[int],
    returned_ids: list[int],
) -> None:
    """Keep items for expected ids that appeared exactly once; later attempts overwrite earlier ones."""
    for item in items:
        if item.id in expected and returned_ids.count(item.id) == 1:
            results[item.id] = item


def _log(message: str) -> None:
    print(f"[draft] {message}", file=sys.stderr)