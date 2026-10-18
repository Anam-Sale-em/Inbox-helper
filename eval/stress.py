"""Load test: push the pipeline to hundreds or thousands of messages with a SIMULATED model.

Usage: python eval/stress.py [--sizes 82 1000 3000 5000] [--speedup 400] [--old]

--old replays the Day 4 flow (one sorting call for everything, then drafting) for comparison.

No API key, no cost. The stand-in model behaves like Gemini 2.5 Flash on the things that
matter at scale (assumptions, not measurements; check the 82-message size against a live run):
  - time per call = 0.8 s + output tokens / 150 per second
  - one answer is capped at 65,536 output tokens; past that the JSON comes back cut off
  - NO rate limit in the timings. The last column shows the floor on Gemini's free tier
    (5 requests/minute, found in the Day 5 live run): calls / 5 minutes
Model waits are divided by --speedup so the test runs quickly, then scaled back up. Local work
(duplicate check, injection check) is timed separately and NOT scaled: scaling it made the first
version of this test report fake delays (Day 5).
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inbox import classify, draft, pipeline  # noqa: E402
from inbox.dedupe import dedupe  # noqa: E402
from inbox.intake import IST  # noqa: E402
from inbox.reschedule import SLOTS_TOKEN  # noqa: E402
from inbox.schema import Message  # noqa: E402

MAX_OUTPUT_TOKENS = 65_536
BASE_SECONDS, TOKENS_PER_SECOND = 0.8, 150
PRICE_IN, PRICE_OUT = 0.30, 2.50  # USD per 1M tokens, Gemini 2.5 Flash paid tier
IDS = re.compile(r'<message id="(\d+)"[^>]*?(?:intent="([^"]*)")?>')


class SimulatedModel:
    def __init__(self, speedup: float):
        self.speedup, self.calls, self.tokens_in, self.tokens_out, self.cut_off = speedup, 0, 0, 0, 0

    def __call__(self, prompt: str, system: str | None = None) -> str:
        found = IDS.findall(prompt)
        if system == classify.SYSTEM_PROMPT:
            items = [{"id": int(i), "intent": "application_status", "confidence": 0.9} for i, _ in found]
        else:
            items = [{"id": int(i), "reply": "Hi there, thanks for your message. " * 6,
                      "alt_reply": "" if intent != "reschedule_interview" else "Hi, we can't move it. " * 4}
                     for i, intent in found]
            for item, (_, intent) in zip(items, found):
                if intent == "reschedule_interview":
                    item["reply"] = f"Hi, we can move it. Slots: {SLOTS_TOKEN}. Reply with one."
        text = json.dumps({"items": items})
        out_tokens = len(text) // 4
        self.calls += 1
        self.tokens_in += (len(prompt) + len(system or "")) // 4
        self.tokens_out += min(out_tokens, MAX_OUTPUT_TOKENS)
        time.sleep((BASE_SECONDS + min(out_tokens, MAX_OUTPUT_TOKENS) / TOKENS_PER_SECOND) / self.speedup)
        if out_tokens > MAX_OUTPUT_TOKENS:  # the real API stops mid-answer: invalid JSON
            self.cut_off += 1
            return text[: MAX_OUTPUT_TOKENS * 4]
        return text


def make_messages(n: int, seed: int = 5) -> list[Message]:
    """n messages from the 82-message set's texts, new senders, ~5% resent as exact duplicates."""
    pool = [m["text"] for m in json.loads((ROOT / "data" / "messages.json").read_text(encoding="utf-8"))
            if m["text"].strip() and not classify.looks_like_injection(m["text"])]
    rng, start = random.Random(seed), datetime(2026, 10, 18, 9, tzinfo=IST)
    messages: list[Message] = []
    for i in range(1, n + 1):
        if messages and rng.random() < 0.05:
            original = rng.choice(messages)
            text, email = original.text, original.from_email
        else:
            text, email = rng.choice(pool), f"candidate{i}@example.com"
        messages.append(Message(id=i, from_name=f"Candidate {i}", from_email=email,
                                received_at=start + timedelta(minutes=i), text=text))
    return messages


def run(n: int, speedup: float, old: bool = False) -> dict:
    model = SimulatedModel(speedup)
    classify.call_llm = draft.call_llm = model
    for module in (classify, draft):
        module.reset_stats()
    messages = make_messages(n)
    local_began = time.perf_counter()
    unique, duplicates = dedupe(messages)
    if not old:
        progress = pipeline.start(unique, context=None)  # injection check + holding replies, no AI
    local = time.perf_counter() - local_began
    began = time.perf_counter()
    if old:  # Day 4: sort everything in one call, then draft
        saved, classify.BATCH_SIZE = classify.BATCH_SIZE, 10**9
        try:
            classes = classify.classify(unique)
        finally:
            classify.BATCH_SIZE = saved
        after_classify = time.perf_counter()
        _, batches = draft.plan_drafts(unique, classes)
        first = None
        for _ in draft.draft_batches(batches, classes):
            first = first or time.perf_counter()
    else:  # Day 5: waves of sorting + drafting
        pipeline.step(progress)
        first = time.perf_counter()
        while progress.to_classify:
            pipeline.step(progress)
        after_classify = time.perf_counter()
        while not progress.done:
            pipeline.step(progress)
        classes = progress.classifications
    end = time.perf_counter()
    failed = sum(1 for c in classes.values() if c.intent == "needs_human" and c.confidence == 0.0)
    scale = lambda seconds: round(local + seconds * speedup, 1)  # noqa: E731
    return {
        "messages": n, "duplicates": len(duplicates),
        "first_cards_s": scale((first or end) - began), "classify_s": scale(after_classify - began),
        "total_s": scale(end - began), "calls": model.calls, "cut_off_answers": model.cut_off,
        "lost_to_needs_human": failed,
        "free_tier_min_minutes": round(model.calls / 5, 1),
        "cost_usd": round(model.tokens_in / 1e6 * PRICE_IN + model.tokens_out / 1e6 * PRICE_OUT, 3),
    }


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--sizes", type=int, nargs="+", default=[82, 1000, 3000, 5000])
    args.add_argument("--speedup", type=float, default=40)
    args.add_argument("--old", action="store_true", help="the Day 4 flow, for comparison")
    args.add_argument("--json")
    opts = args.parse_args()
    rows = [run(n, opts.speedup, opts.old) for n in opts.sizes]
    flow = "Day 4 flow: one sorting call" if opts.old else "Day 5 flow: waves of 50-message sorting + drafting"
    print(f"SIMULATED model (assumptions at the top of this file). {flow}\n")
    print(f"{'messages':>8} {'first cards':>12} {'sorted by':>9} {'total':>8} {'calls':>6} "
          f"{'cut off':>8} {'lost':>6} {'cost $':>7} {'free tier':>10}")
    for r in rows:
        print(f"{r['messages']:>8} {r['first_cards_s']:>11}s {r['classify_s']:>8}s {r['total_s']:>7}s "
              f"{r['calls']:>6} {r['cut_off_answers']:>8} {r['lost_to_needs_human']:>6} {r['cost_usd']:>7} "
              f"{r['free_tier_min_minutes']:>8}min")
    print("\nlost = messages that ended up in needs_human because the sorting answer failed")
    print("free tier = minimum minutes at Gemini's free 5 requests/minute (paid tiers allow far more)")
    if opts.json:
        Path(opts.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
