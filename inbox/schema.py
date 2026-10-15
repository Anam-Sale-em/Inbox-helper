"""Pydantic v2 models for inbox messages and LLM responses, plus id round-trip checks."""

from collections import Counter
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Intent = Literal[
    "reschedule_interview",
    "application_status",
    "salary_question",
    "offer_response",
    "withdrawal",
    "document_submission",
    "other",
    "needs_human",
]

MAX_REPLY_WORDS = 80


class Message(BaseModel):
    id: int
    from_name: str
    from_email: str
    received_at: datetime
    text: str


class Classification(BaseModel):
    id: int
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)


class ClassificationResponse(BaseModel):
    items: list[Classification]


class Draft(BaseModel):
    id: int
    reply: str
    alt_reply: str = ""  # reschedule only: the "can't change the time" version

    @field_validator("reply", "alt_reply")
    @classmethod
    def reply_within_word_limit(cls, value: str) -> str:
        word_count = len(value.split())
        if word_count > MAX_REPLY_WORDS:
            raise ValueError(
                f"reply has {word_count} words, max is {MAX_REPLY_WORDS}"
            )
        return value


class DraftResponse(BaseModel):
    items: list[Draft]


def check_ids(expected_ids: set[int], returned_ids: list[int]) -> list[str]:
    """Return readable errors for missing, extra and repeated ids; empty list if all good."""
    counts = Counter(returned_ids)
    returned_set = set(counts)

    missing = sorted(expected_ids - returned_set)
    extra = sorted(returned_set - expected_ids)
    repeated = sorted(i for i, n in counts.items() if n > 1)

    errors: list[str] = []
    if missing:
        errors.append(f"Missing ids (must appear exactly once): {missing}")
    if extra:
        errors.append(f"Extra ids that were not sent: {extra}")
    if repeated:
        details = ", ".join(f"{i} ({counts[i]}x)" for i in repeated)
        errors.append(f"Repeated ids (must appear only once): {details}")
    return errors
