"""Recruiter facts: drafts use the status/salary facts; interview slots stay with the approver."""

import json

from inbox import draft as draft_module
from inbox.checks import run_checks, unlisted_times
from inbox.context import RecruiterContext, load_context
from inbox.draft import REVISE_SYSTEM_PROMPT, draft_replies, revise_reply
from inbox.schema import Classification
from tests.conftest import make_message

SLOTS = ["Fri 23 Oct, 11:00 AM", "Fri 23 Oct, 3:00 PM"]
CONTEXT = RecruiterContext(status_update="Update within 5 working days.")


def recording(monkeypatch, reply="Hi Asha, update within 5 working days."):
    calls = []

    def fake(prompt, system=None):
        calls.append((prompt, system))
        return json.dumps({"items": [{"id": 1, "reply": reply}]})

    monkeypatch.setattr(draft_module, "call_llm", fake)
    return calls


def test_policy_facts_reach_the_prompt(monkeypatch):
    calls = recording(monkeypatch)

    draft_replies([make_message(1)], {1: Classification(id=1, intent="application_status", confidence=0.9)}, CONTEXT)

    prompt, system = calls[0]
    assert "<recruiter_facts>" in prompt and "5 working days" in prompt
    assert "Never write a specific date or time yourself" in system


def test_revision_gets_the_policy_facts(monkeypatch):
    calls = recording(monkeypatch)
    revise_reply(make_message(1), "application_status", "old", "Mention the timeline", CONTEXT)

    prompt, system = calls[0]
    assert system is REVISE_SYSTEM_PROMPT and "5 working days" in prompt


def test_listed_times_pass_and_other_times_are_flagged():
    assert unlisted_times("We have 11 am or 3pm on Friday.", SLOTS) == []
    assert unlisted_times("How about 4:30 PM?", SLOTS) == ["4:30 PM"]
    assert unlisted_times("Is 15:00 ok?", SLOTS) == []          # 24-hour form of 3 PM
    assert "unlisted_time" in run_checks("Can you do 10 AM Friday?", [], SLOTS)


def test_slot_check_is_skipped_without_slots_and_ignores_non_times():
    assert "unlisted_time" not in run_checks("Can you do 10 AM?", [], None)
    assert unlisted_times("We'll update you within 5 working days.", SLOTS) == []


def test_missing_facts_file_means_no_facts(tmp_path):
    assert load_context(tmp_path / "nope.json") == RecruiterContext()


def test_old_facts_file_with_slot_keys_still_loads(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"interview_slots": SLOTS, "reschedule_allowed": False,
                                "status_update": "5 days"}))
    assert load_context(path) == RecruiterContext(status_update="5 days")
