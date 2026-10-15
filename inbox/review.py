"""Terminal review step: the recruiter approves, edits or rejects each draft. Sends nothing."""

from typing import Callable

from inbox.reschedule import approval_blocker, unwrap_filled_blanks
from inbox.schema import Draft, Message

NO_DRAFT = "NO DRAFT — handle manually"
DIVIDER = "-" * 60


def review(
    groups: dict[str, list[Message]],
    drafts: dict[int, Draft],
    flags: dict[int, list[str]],
    input_fn: Callable[[str], str] = input,
) -> list[dict]:
    """Walk through every message and return approved items as {id, to, reply, edited}."""
    _print_summary(groups)

    approved: list[dict] = []
    total = sum(len(messages) for messages in groups.values())
    position = 0

    for intent, messages in groups.items():
        for message in messages:
            position += 1
            draft = drafts.get(message.id)
            reply = draft.reply if draft else ""
            _print_message(position, total, intent, message, reply, flags.get(message.id, []))

            item = _ask(message, reply, input_fn, offering_slots=intent == "reschedule_interview")
            if item is not None:
                approved.append(item)

    print(f"\n{DIVIDER}\nApproved {len(approved)} of {total}. Nothing has been sent.")
    return approved


def _print_summary(groups: dict[str, list[Message]]) -> None:
    print("Summary")
    for intent, messages in groups.items():
        print(f"  {intent}: {len(messages)}")


def _print_message(
    position: int,
    total: int,
    intent: str,
    message: Message,
    reply: str,
    message_flags: list[str],
) -> None:
    print(f"\n{DIVIDER}")
    print(f"[{position}/{total}] intent: {intent}")
    print(f"From: {message.from_name} <{message.from_email}>")
    print(f"Message:\n{message.text}")
    print(f"\nDraft:\n{reply if reply.strip() else NO_DRAFT}")
    for flag in message_flags:
        print(f"⚠ {flag}")


def _ask(
    message: Message, reply: str, input_fn: Callable[[str], str], offering_slots: bool = False
) -> dict | None:
    """Loop until a valid key; return the approved item, or None if rejected."""
    while True:
        key = input_fn("[a]pprove  [e]dit  [r]eject: ").strip().lower()

        if key == "a":
            if not reply.strip():
                print("Draft is empty. Edit it or reject.")
                continue
            blocker = approval_blocker(reply, offering_slots)
            if blocker:
                print(blocker.split(", then press")[0] + ". Press e to write the reply.")
                continue
            return _item(message, reply, edited=False)

        if key == "e":
            new_reply = unwrap_filled_blanks(input_fn("New reply (one line): ").strip())
            if not new_reply:
                print("Empty reply, not saved.")
                continue
            return _item(message, new_reply, edited=True)

        if key == "r":
            return None

        print("Please type a, e or r.")


def _item(message: Message, reply: str, edited: bool) -> dict:
    return {"id": message.id, "to": message.from_email, "reply": reply, "edited": edited}
