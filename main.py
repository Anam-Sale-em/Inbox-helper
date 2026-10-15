"""Inbox Helper entry point: runs the full pipeline from directive.md."""

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import get_args

from pydantic import TypeAdapter

from inbox import classify, draft, llm
from inbox.checks import run_checks
from inbox.context import DEFAULT_PATH, load_context
from inbox.dedupe import dedupe
from inbox.review import review
from inbox.schema import Classification, Draft, Intent, Message


def main() -> None:
    args = parse_args()
    if args.mock:
        os.environ["LLM_PROVIDER"] = "mock"

    messages = load_messages(args.input)
    unique, duplicates = dedupe(messages)
    classifications = classify.classify(unique)
    groups = group_by_intent(unique, classifications)
    context = load_context(args.context)
    drafts = draft.draft_replies(unique, classifications, context)
    flags = check_drafts(unique, drafts, [])  # terminal: approver types slots with [e]dit

    # Saved before review so a Ctrl-C during review keeps the pipeline output.
    run_dir = make_run_dir()
    save(run_dir, classifications=dump(classifications), drafts=dump(drafts),
         flags=flags, duplicates=duplicates)

    outbox = review(groups, drafts, flags)
    report = build_report(messages, duplicates, classifications, drafts, flags)
    save(run_dir, outbox=outbox, report=report)
    print_summary(report, run_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inbox Helper")
    parser.add_argument("--input", default="data/messages.json")
    parser.add_argument("--mock", action="store_true", help="replay recorded LLM responses")
    parser.add_argument("--context", default=str(DEFAULT_PATH),
                        help="recruiter facts: interview slots, reschedule policy, status timeline")
    return parser.parse_args()


def load_messages(path: str) -> list[Message]:
    raw = Path(path).read_text(encoding="utf-8")
    return TypeAdapter(list[Message]).validate_json(raw)


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
        parts = name.split()
        return {name.strip(), parts[0]} if parts else set()

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
    print(f"Run saved to {run_dir}")


if __name__ == "__main__":
    main()
