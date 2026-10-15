"""Drop exact and near-duplicate messages per sender, keeping the earliest copy."""

import re
from difflib import SequenceMatcher

from inbox.schema import Message

NEAR_DUPLICATE_RATIO = 0.9

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, trim."""
    text = _PUNCTUATION.sub("", text.lower())
    return _WHITESPACE.sub(" ", text).strip()


def dedupe(messages: list[Message]) -> tuple[list[Message], dict[int, int]]:
    """Return (unique messages, {duplicate_id: original_id})."""
    # id breaks ties so equal timestamps still give a stable "earliest".
    ordered = sorted(messages, key=lambda m: (m.received_at, m.id))

    unique: list[Message] = []
    duplicates: dict[int, int] = {}
    exact_seen: dict[tuple[str, str], int] = {}
    kept_by_sender: dict[str, list[tuple[int, str]]] = {}

    for message in ordered:
        sender = message.from_email.strip().lower()
        text = normalize(message.text)

        original_id = exact_seen.get((sender, text))
        if original_id is None:
            original_id = _find_near(text, kept_by_sender.get(sender, []))

        if original_id is not None:
            duplicates[message.id] = original_id
            continue

        unique.append(message)
        exact_seen[(sender, text)] = message.id
        kept_by_sender.setdefault(sender, []).append((message.id, text))

    return unique, duplicates


def _find_near(text: str, candidates: list[tuple[int, str]]) -> int | None:
    """Return the earliest kept message id with ratio >= 0.9, else None."""
    for original_id, original_text in candidates:
        # autojunk off: it skews ratios on texts longer than 200 chars.
        matcher = SequenceMatcher(None, original_text, text, autojunk=False)
        if (
            matcher.real_quick_ratio() >= NEAR_DUPLICATE_RATIO
            and matcher.quick_ratio() >= NEAR_DUPLICATE_RATIO
            and matcher.ratio() >= NEAR_DUPLICATE_RATIO
        ):
            return original_id
    return None
