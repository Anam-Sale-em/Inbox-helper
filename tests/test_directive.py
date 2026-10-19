"""The directive's hard rules, checked in code instead of trusted from a document (Day 4).
If one of these fails, either the code broke a rule or the rule changed: update directive.md too."""

import re
from pathlib import Path
from typing import get_args

ROOT = Path(__file__).resolve().parent.parent
# tools/ holds one-off diagnostic scripts (e.g. the Day 1 key check), not the pipeline.
CODE = [p for p in ROOT.rglob("*.py")
        if not {"tests", "tools", ".venv", "venv", "site-packages"} & set(p.relative_to(ROOT).parts)]


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_only_llm_py_talks_to_a_model_sdk():
    sdk = re.compile(r"^\s*(?:from|import)\s+(?:groq|google\.genai|google import genai|openai|anthropic)\b", re.M)
    users = [p.relative_to(ROOT).as_posix() for p in CODE if sdk.search(_source(p))]
    assert users == ["inbox/llm.py"]


def test_no_model_names_in_code():
    model = re.compile(r"[\"'](?:gemini-[\w.-]+|gpt-[\w.-]+|openai/[\w.-]+|llama[\w.-]*)[\"']", re.I)
    assert [p.name for p in CODE if model.search(_source(p))] == []


def test_nothing_in_the_code_can_send_email():
    sender = re.compile(r"\bimport\s+smtplib\b|\bsmtplib\.|\.messages\(\)\.send\(|\.drafts\(\)|\.send_message\(")
    assert [p.name for p in CODE if sender.search(_source(p))] == []


def test_google_access_is_read_only():
    # v2.2: Gmail and Calendar are read-only. The token can't send, delete or edit anything,
    # and the calendar scope sees free/busy only, not event titles or guests.
    from inbox.google_auth import SCOPES
    assert SCOPES == ["https://www.googleapis.com/auth/gmail.readonly",
                      "https://www.googleapis.com/auth/calendar.freebusy"]


def test_the_ai_never_sees_the_calendar():
    # v2.2: free slots fill the reply's blank after drafting; nothing calendar-related reaches a prompt.
    for name in ("inbox/llm.py", "inbox/draft.py", "inbox/classify.py", "inbox/pipeline.py"):
        assert "calendar" not in (ROOT / name).read_text(encoding="utf-8").lower(), name


def test_exactly_the_eight_intents():
    from inbox.schema import Intent
    assert set(get_args(Intent)) == {
        "reschedule_interview", "application_status", "salary_question", "offer_response",
        "withdrawal", "document_submission", "other", "needs_human"}


def test_every_prompt_treats_candidate_text_as_untrusted():
    from inbox import classify, draft
    for prompt in (classify.SYSTEM_PROMPT, draft.SYSTEM_PROMPT, draft.REVISE_SYSTEM_PROMPT):
        assert "untrusted" in prompt.lower() and "<message" in prompt


def test_word_limit_matches_the_prompts():
    from inbox import draft
    from inbox.schema import MAX_REPLY_WORDS
    assert MAX_REPLY_WORDS == 80
    assert "Under 80 words" in draft.SYSTEM_PROMPT and "Under 80 words" in draft.REVISE_SYSTEM_PROMPT


def test_first_drafts_never_write_a_time():
    from inbox import draft
    assert "Never write a specific date or time yourself" in draft.SYSTEM_PROMPT


def test_batching_matches_the_directive():
    from inbox import classify, draft
    assert (draft.BATCH_SIZE, draft.PARALLEL_BATCHES) == (5, 3)
    assert (classify.BATCH_SIZE, classify.PARALLEL_BATCHES) == (50, 3)  # v2.0: no single sorting call
