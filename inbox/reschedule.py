"""Reschedule replies: the AI writes two versions once, and the approver picks one.

- "offer": agrees to move the interview, with a blank (SLOTS_TOKEN) where the approver
  types the times straight into the reply. The AI never writes a time itself.
- "decline": politely says the interview time can't be changed.

Switching between them needs no further AI call.
"""

import re

SLOTS_TOKEN = "[add time slots]"

# Any "[...]" left in a reply is an unfilled blank: today's token, an older one ("[SLOTS]")
# from a previous run, or one the AI made up ("[time]"). Nothing in square brackets is
# meant to reach a candidate, so approval is blocked until it's gone.
_PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,40}\]")


def unfilled_blanks(reply: str) -> list[str]:
    """Every [placeholder] still in the reply."""
    return _PLACEHOLDER.findall(reply)


def unwrap_filled_blanks(reply: str) -> str:
    """Drop the brackets around a blank the approver has typed times into.

    People naturally type inside the brackets: "[Friday 1 pm ]" becomes "Friday 1 pm".
    Brackets with no time inside (still a real blank) are left alone.
    """
    from inbox.checks import times_in

    def unwrap(match):
        inner = match.group(0)[1:-1].strip()
        return inner if times_in(inner) else match.group(0)

    return _PLACEHOLDER.sub(unwrap, reply)


def approval_blocker(reply: str, offering_slots: bool) -> str | None:
    """Why this reply can't be approved yet, or None if it can.

    offering_slots: the reply agrees to move the interview, so it must name at least one
    actual time. Deleting the blank without typing a time ("here are the slots for Friday.")
    is caught here, not just a leftover [placeholder].
    """
    from inbox.checks import times_in  # local import keeps module dependencies one-way

    blanks = unfilled_blanks(reply)
    if blanks:
        return (f"Replace {', '.join(dict.fromkeys(blanks))} with the times you can offer, "
                "then press Ctrl+Enter (or click outside the box) to apply.")
    if offering_slots and not times_in(reply):
        return ("Add at least one time you can offer (e.g. \"Fri 23 Oct, 11:00 AM\"), "
                "then press Ctrl+Enter (or click outside the box) to apply.")
    return None


def offer_problem(reply: str, alt_reply: str) -> str | None:
    """Why a model-written reschedule pair is unusable, or None if it's fine."""
    if reply.count(SLOTS_TOKEN) != 1:
        return (f"reply must contain the exact token {SLOTS_TOKEN} once, where the slots go, "
                "and no times of its own")
    if not alt_reply.strip():
        return "alt_reply must be the polite 'the interview time can't be changed' version"
    return None


def fallback_offer(first_name: str) -> str:
    return (f"Hi {first_name}, thanks for letting us know. We can move your interview. "
            f"These slots are available: {SLOTS_TOKEN}. Please reply with the one that suits you.")


def fallback_decline(first_name: str) -> str:
    return (f"Hi {first_name}, thanks for letting us know. Unfortunately we're not able to change "
            "the interview time. Please let us know if you can still attend at the original time.")
