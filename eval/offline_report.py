"""Score the parts that don't need an AI call, against the answer key. No API key needed.

Usage: python eval/offline_report.py [--json out.json]

Covers: loading, empty messages, duplicates, injection pre-check (main set + hold-out set),
first names, and messages that need no reply. The AI parts (intents, drafts) are scored by
eval/day1_report.py after a live run.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inbox.classify import looks_like_injection  # noqa: E402
from inbox.dedupe import dedupe  # noqa: E402
from main import load_report, split_empty  # noqa: E402

try:  # added on Day 4; the "before" numbers were taken without them
    from inbox.names import first_name
except ImportError:  # pragma: no cover
    from inbox.draft import _first_name as _old

    def first_name(raw: str) -> str:
        class _M:  # the old helper took a Message
            from_name = raw
        return _old(_M())

try:
    from main import split_no_reply
except ImportError:  # pragma: no cover
    def split_no_reply(messages):
        return messages, {}

DATA = ROOT / "data"
INJECTION_TYPES = ("injection", "injection_obfuscated")


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--json", help="also save the numbers to this file")
    out = args.parse_args()

    result = {"main_set": score_main(), "holdout": score_holdout()}
    print_report(result)
    if out.json:
        Path(out.json).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def score_main() -> dict:
    expected = {e["id"]: e for e in json.loads((DATA / "expected.json").read_text(encoding="utf-8"))}
    loaded = load_report(str(DATA / "messages.json"))
    usable, empty = split_empty(loaded.messages)
    to_reply, no_reply = split_no_reply(usable)
    unique, duplicates = dedupe(to_reply)

    want_dups = {i: e["duplicate_of"] for i, e in expected.items() if e.get("duplicate_of")}
    caught = sorted(set(want_dups) & set(duplicates))
    false_dups = sorted(set(duplicates) - set(want_dups))

    originals = [m for m in unique if m.id not in want_dups]
    targets = [m for m in originals if expected[m.id].get("attack") in INJECTION_TYPES]
    others = [m for m in originals if expected[m.id].get("attack") not in INJECTION_TYPES]
    inj_caught = [m.id for m in targets if looks_like_injection(m.text)]
    inj_missed = [m.id for m in targets if m.id not in inj_caught]
    false_alarms = [m.id for m in others if looks_like_injection(m.text)]

    by_id = {m.id: m for m in loaded.messages}
    names = {i: (e["first_name"], first_name(by_id[i].from_name))
             for i, e in expected.items() if "first_name" in e}
    want_no_reply = {i for i, e in expected.items() if e.get("no_reply")}

    return {
        "messages": len(loaded.messages),
        "duplicates": {"caught": len(caught), "total": len(want_dups), "false": false_dups},
        "injection_precheck": {"caught": len(inj_caught), "total": len(targets),
                               "missed": inj_missed, "false_alarms": false_alarms},
        "first_names": {"right": sum(w == g for w, g in names.values()), "total": len(names),
                        "wrong": {i: {"want": w, "got": g} for i, (w, g) in names.items() if w != g}},
        "no_reply": {"caught": len(set(no_reply) & want_no_reply), "total": len(want_no_reply),
                     "missed": sorted(want_no_reply - set(no_reply)),
                     "false": sorted(set(no_reply) - want_no_reply)},
        "empty_skipped": len(empty),
    }


def score_holdout() -> dict:
    messages = load_report(str(DATA / "holdout_attacks.json")).messages
    expected = {e["id"]: e for e in json.loads((DATA / "holdout_expected.json").read_text(encoding="utf-8"))}
    attacks = [m for m in messages if expected[m.id]["attack"]]
    benign = [m for m in messages if not expected[m.id]["attack"]]
    return {
        "caught": sum(looks_like_injection(m.text) for m in attacks), "total": len(attacks),
        "missed": [expected[m.id]["note"] for m in attacks if not looks_like_injection(m.text)],
        "false_alarms": [expected[m.id]["note"] for m in benign if looks_like_injection(m.text)],
        "benign_total": len(benign),
    }


def print_report(r: dict) -> None:
    m, h = r["main_set"], r["holdout"]
    print(f"Main set ({m['messages']} messages)")
    print(f"  Duplicates caught     {m['duplicates']['caught']}/{m['duplicates']['total']}"
          f"   false duplicates: {m['duplicates']['false'] or 'none'}")
    inj = m["injection_precheck"]
    print(f"  Injection pre-check   {inj['caught']}/{inj['total']}   missed: {inj['missed']}"
          f"   false alarms: {inj['false_alarms'] or 'none'}")
    fn = m["first_names"]
    print(f"  First names           {fn['right']}/{fn['total']}   wrong: {fn['wrong'] or 'none'}")
    nr = m["no_reply"]
    print(f"  No reply needed       {nr['caught']}/{nr['total']}   missed: {nr['missed'] or 'none'}"
          f"   false: {nr['false'] or 'none'}")
    print(f"  Empty skipped         {m['empty_skipped']}")
    print("Hold-out attacks (not used to write the fixes)")
    print(f"  Injection pre-check   {h['caught']}/{h['total']}   missed: {h['missed'] or 'none'}")
    print(f"  False alarms          {len(h['false_alarms'])}/{h['benign_total']}   {h['false_alarms'] or ''}")


if __name__ == "__main__":
    main()
