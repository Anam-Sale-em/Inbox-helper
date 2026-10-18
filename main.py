"""Inbox Helper entry point: runs the full pipeline from directive.md."""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import get_args


from inbox import classify, draft, llm
from inbox.checks import run_checks
from inbox.context import DEFAULT_PATH, load_context
from inbox.dedupe import dedupe
from inbox.intake import LoadReport, load_file
from inbox.names import first_name
from inbox.review import review
from inbox.schema import Classification, Draft, Intent, Message


def main() -> None:
    args = parse_args()
    if args.mock:
        os.environ["LLM_PROVIDER"] = "mock"

    loaded = load_report(args.input)
    messages = loaded.messages
    for r in loaded.rejected:
        print(f"Skipped record {r['position']}: {r['reason']}")
    for r in loaded.renumbered:
        print(f"Id {r['old_id']} was already used by {r['kept_by']}: {r['from_email']} gets id {r['new_id']}")
    usable, empty = split_empty(messages)
    for m in empty:
        print(f"Skipped #{m.id} from {m.from_email}: no text")
    usable, no_reply = split_no_reply(usable)
    for msg_id, reason in no_reply.items():
        print(f"No reply needed for #{msg_id}: {reason}")
    unique, duplicates = dedupe(usable)
    context = load_context(args.context)
    try:
        classifications = classify.classify(unique)
        drafts = draft.draft_replies(unique, classifications, context)
    except llm.MockResponseMissing:
        # Mock mode only replays what a live run recorded; say so instead of a traceback.
        print("Mock mode has no recorded response for this input. Record one with a live run:\n"
              '  $env:RECORD="1"; python main.py --input ' + args.input, file=sys.stderr)
        sys.exit(2)
    groups = group_by_intent(unique, classifications)
    flags = check_drafts(unique, drafts, [])  # terminal: approver types slots with [e]dit

    # Saved before review so a Ctrl-C during review keeps the pipeline output.
    run_dir = make_run_dir()
    save(run_dir, classifications=dump(classifications), drafts=dump(drafts),
         flags=flags, duplicates=duplicates, skipped_empty=[m.id for m in empty],
         skipped_no_reply=no_reply,
         skipped_records=loaded.rejected, renumbered=loaded.renumbered, ai_calls=llm.get_usage(),
         input_messages=[m.model_dump(mode="json") for m in messages])  # scorers read this, not data/

    held: list[dict] = []
    outbox = review(groups, drafts, flags, held=held)
    report = build_report(messages, duplicates, classifications, drafts, flags)
    save(run_dir, outbox=outbox, on_hold=held, report=report)
    print_summary(report, run_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inbox Helper")
    parser.add_argument("--input", default="data/messages.json")
    parser.add_argument("--mock", action="store_true", help="replay recorded LLM responses")
    parser.add_argument("--context", default=str(DEFAULT_PATH),
                        help="recruiter facts: interview slots, reschedule policy, status timeline")
    return parser.parse_args()


def load_report(path: str) -> LoadReport:
    """Load every readable message from a JSON file. See inbox/intake.py for the rules."""
    return load_file(path)


def load_messages(path: str) -> list[Message]:
    return load_report(path).messages


def split_empty(messages: list[Message]) -> tuple[list[Message], list[Message]]:
    """Messages with no text are skipped before any AI call: there's nothing to sort or answer.
    They're returned separately so the recruiter still sees they arrived."""
    kept = [m for m in messages if m.text.strip()]
    empty = [m for m in messages if not m.text.strip()]
    return kept, empty


_NO_REPLY_SENDER = re.compile(r"^(?:mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounces?)@", re.I)
_AUTO_TEXT = re.compile(
    r"^\s*(?:automatic reply|auto[- ]?reply|autoreply|out of (?:the )?office\s*:|"
    r"delivery status notification|undeliverable\s*:|mail delivery failed)", re.I)


def no_reply_reason(message: Message) -> str | None:
    """Auto-replies and bounces: nobody is waiting for an answer, so no AI call and no draft."""
    if _NO_REPLY_SENDER.match(message.from_email.strip()):
        return "Sent from an address that doesn't take replies"
    if _AUTO_TEXT.match(message.text):
        return "Automatic reply or bounce"
    return None


def split_no_reply(messages: list[Message]) -> tuple[list[Message], dict[int, str]]:
    kept, skipped = [], {}
    for m in messages:
        reason = no_reply_reason(m)
        if reason:
            skipped[m.id] = reason
        else:
            kept.append(m)
    return kept, skipped


def group_by_intent(
    messages: list[Message], classifications: dict[int, Classification]
) -> dict[str, list[Message]]:
    groups: dict[str, list[Message]] = {intent: [] for intent in get_args(Intent)}
    for message in messages:
        groups[classifications[message.id].intent].append(message)
    return {intent: items for intent, items in groups.items() if items}


def check_drafts(
    messages: list[Message], drafts: dict[int, Draft], allowed_slots: list[str] | None = None
) -> dict[int, list[str]]:
    flags = {}
    for message in messages:
        reply = drafts[message.id].reply
        if reply.strip():
            flags[message.id] = run_checks(reply, other_names(messages, message), allowed_slots)
    return {msg_id: found for msg_id, found in flags.items() if found}


def other_names(messages: list[Message], sender: Message) -> list[str]:
    """Full and first names of everyone else, minus any the sender shares."""
    def variants(name: str) -> set[str]:
        # Same first-name rules as greetings ("KUMAR, VARUN" -> "Varun"), so a sender who shares
        # a first name with another candidate isn't flagged for their own greeting.
        parts = name.split()
        if not parts:
            return set()
        found = {name.strip(), parts[0].strip(",")}
        first = first_name(name)
        return found | ({first} if first != "there" else set())

    own = {n.lower() for n in variants(sender.from_name)}
    others = set().union(*(variants(m.from_name) for m in messages
                           if m.from_email != sender.from_email))
    return sorted(n for n in others if n.lower() not in own)


def build_report(messages, duplicates, classifications, drafts, flags) -> dict:
    intents = [c.intent for c in classifications.values()]
    llm_stats = llm.get_stats()
    return {
        "total_messages": len(messages),
        "duplicates_found": len(duplicates),
        "intent_counts": dict(Counter(intents)),
        "flagged_by_type": dict(Counter(f for found in flags.values() for f in found)),
        "needs_human": intents.count("needs_human"),
        "empty_drafts": sum(not d.reply.strip() for d in drafts.values()),
        "retries": {
            "classify": classify.get_stats()["retries"],
            "draft": draft.get_stats()["retries"],
            "http": llm_stats["retries"],
        },
        "provider_fallbacks": llm_stats["fallbacks"],
        "providers_used": llm_stats["provider_used"],
        "ai_usage": llm.usage_summary(),
        "batching": {"batch_size": draft.BATCH_SIZE, "parallel": draft.PARALLEL_BATCHES},
    }


def make_run_dir() -> Path:
    run_dir = Path("runs") / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def dump(models: dict) -> dict:
    return {msg_id: model.model_dump(mode="json") for msg_id, model in models.items()}


def save(run_dir: Path, **files: object) -> None:
    for name, data in files.items():
        text = json.dumps(data, indent=2, ensure_ascii=False)
        (run_dir / f"{name}.json").write_text(text, encoding="utf-8")


def print_summary(report: dict, run_dir: Path) -> None:
    print(f"\nMessages: {report['total_messages']}  duplicates: {report['duplicates_found']}  "
          f"needs_human: {report['needs_human']}  empty drafts: {report['empty_drafts']}")
    print(f"Flags: {report['flagged_by_type'] or 'none'}  fallbacks: {report['provider_fallbacks']}")
    u = report["ai_usage"]
    print(f"AI: {u['calls']} calls, {u['input_tokens']} tokens in, {u['output_tokens']} out, "
          f"{u['thinking_tokens']} thinking, slowest call {u['slowest_call_seconds']}s")
    print(f"Run saved to {run_dir}")


if __name__ == "__main__":
    main()
