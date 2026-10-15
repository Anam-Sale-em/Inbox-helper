"""Reschedule: the AI writes both versions once; the approver types the times into the reply."""

import json

from inbox import draft as draft_module
from inbox.draft import draft_replies, revise_reply
from inbox.reschedule import SLOTS_TOKEN, approval_blocker, unfilled_blanks, unwrap_filled_blanks
from inbox.review import review
from inbox.schema import Classification, Draft
from tests.conftest import make_message

RESCHEDULE = {1: Classification(id=1, intent="reschedule_interview", confidence=0.9)}
OFFER = f"Hi Asha, happy to move it. These slots are open: {SLOTS_TOKEN}. Which suits you?"
DECLINE = "Hi Asha, sorry, we can't change the interview time. Can you still make the original slot?"


def fake_drafter(monkeypatch, *responses):
    queue, calls = iter(responses), []

    def fake(prompt, system=None):
        calls.append(prompt)
        return json.dumps(next(queue))

    monkeypatch.setattr(draft_module, "call_llm", fake)
    return calls


def test_both_versions_come_back_from_one_call(monkeypatch):
    calls = fake_drafter(monkeypatch, {"items": [{"id": 1, "reply": OFFER, "alt_reply": DECLINE}]})

    result = draft_replies([make_message(1)], RESCHEDULE)[1]

    assert len(calls) == 1
    assert result.reply == OFFER and result.alt_reply == DECLINE


def test_offer_with_invented_time_is_retried(monkeypatch):
    invented = {"items": [{"id": 1, "reply": "Hi Asha, can you do Friday 3 PM?", "alt_reply": DECLINE}]}
    good = {"items": [{"id": 1, "reply": OFFER, "alt_reply": DECLINE}]}
    calls = fake_drafter(monkeypatch, invented, good)

    result = draft_replies([make_message(1)], RESCHEDULE)[1]

    assert len(calls) == 2 and SLOTS_TOKEN in calls[1]     # the error named the token
    assert result.reply == OFFER


def test_falls_back_to_standard_wording_instead_of_an_empty_card(monkeypatch):
    bad = {"items": [{"id": 1, "reply": "Hi Asha, Friday 3 PM works.", "alt_reply": ""}]}
    fake_drafter(monkeypatch, bad, bad, bad)

    result = draft_replies([make_message(1)], RESCHEDULE)[1]

    assert SLOTS_TOKEN in result.reply and "Hi Asha" in result.reply
    assert "not able to change" in result.alt_reply


def test_other_intents_get_no_second_version(monkeypatch):
    fake_drafter(monkeypatch, {"items": [{"id": 1, "reply": "Hi Asha, noted.", "alt_reply": "stray"}]})

    result = draft_replies([make_message(1)], {1: Classification(id=1, intent="other", confidence=0.9)})[1]

    assert result.alt_reply == ""


def test_revision_keeps_the_slot_token_unless_recruiter_gives_times(monkeypatch):
    lost = {"items": [{"id": 1, "reply": "Hi Asha, shorter now. Which suits you?"}]}
    kept = {"items": [{"id": 1, "reply": f"Hi Asha, open slots: {SLOTS_TOKEN}. Which suits you?"}]}
    fake_drafter(monkeypatch, lost, kept)
    assert SLOTS_TOKEN in revise_reply(make_message(1), "reschedule_interview", OFFER, "Make it shorter").reply

    with_times = {"items": [{"id": 1, "reply": "Hi Asha, can you do Friday at 3 PM?"}]}
    fake_drafter(monkeypatch, with_times)
    revised = revise_reply(make_message(1), "reschedule_interview", OFFER, "Just offer Friday 3 PM")
    assert revised.reply == "Hi Asha, can you do Friday at 3 PM?"


def test_terminal_review_blocks_unfilled_slots():
    message = make_message(1)
    answers = iter(["a", "r"])  # approve is refused, then rejected
    approved = review({"reschedule_interview": [message]}, {1: Draft(id=1, reply=OFFER)}, {},
                      input_fn=lambda prompt: next(answers))
    assert approved == []


def test_any_leftover_blank_blocks_approval():
    assert unfilled_blanks(f"Slots: {SLOTS_TOKEN}. Which suits you?") == [SLOTS_TOKEN]
    assert unfilled_blanks("Please review the available slots: [SLOTS] and reply.") == ["[SLOTS]"]
    assert unfilled_blanks("Can you do [time] on [date]?") == ["[time]", "[date]"]
    assert unfilled_blanks("Slots: Fri 23 Oct, 11:00 AM or 3:00 PM. Which suits you?") == []


def test_offer_needs_an_actual_time_not_just_a_deleted_blank():
    deleted = "Hi Riya, here are the available slots for Friday. Please let us know which works."
    assert "at least one time" in approval_blocker(deleted, offering_slots=True)
    assert approval_blocker("Hi Riya, we have Friday at 1 pm or 3:30 PM.", offering_slots=True) is None
    # the "can't move it" version and other replies don't need a time
    assert approval_blocker("Hi Riya, sorry, we can't change the time.", offering_slots=False) is None
    assert "Replace [SLOTS]" in approval_blocker("Slots: [SLOTS].", offering_slots=False)


def test_times_typed_inside_the_brackets_are_unwrapped():
    typed = "These slots are available: [Friday 1 pm ]. Please reply with the one that suits you."
    assert unwrap_filled_blanks(typed) == "These slots are available: Friday 1 pm. Please reply with the one that suits you."
    assert unwrap_filled_blanks("Slots: [Fri 11:00 AM or 3 PM]") == "Slots: Fri 11:00 AM or 3 PM"
    # a blank with no time in it is still a blank
    assert unwrap_filled_blanks(f"Slots: {SLOTS_TOKEN}") == f"Slots: {SLOTS_TOKEN}"
    assert unwrap_filled_blanks("Slots: [Friday]") == "Slots: [Friday]"


def test_prompts_use_the_same_token_the_code_checks():
    # Bug found 16 Oct: prompts said "[SLOTS]" while the check wanted "[add time slots]",
    # so every live reschedule draft failed validation and fell back to standard wording.
    from inbox.draft import REVISE_SYSTEM_PROMPT, SYSTEM_PROMPT
    assert SLOTS_TOKEN in SYSTEM_PROMPT and SLOTS_TOKEN in REVISE_SYSTEM_PROMPT
    assert "[SLOTS]" not in SYSTEM_PROMPT + REVISE_SYSTEM_PROMPT
    assert '{"items"' in SYSTEM_PROMPT  # braces survived the f-string
