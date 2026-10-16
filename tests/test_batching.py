"""Batches of 5 in parallel (17 Oct): same drafts, same duplicates, every message exactly once."""

import json
import re
import threading
import time

import pytest

from inbox import draft as draft_module
from inbox import llm
from inbox.dedupe import dedupe
from inbox.schema import Classification
from tests.conftest import make_message

IDS_IN_PROMPT = re.compile(r'<message id="(\d+)"')


def _inbox():
    """20 senders; three of them sent an exact copy later, far from the original."""
    messages = [make_message(i, f"Any update on my application? (ref {i})", name=f"Person {i}",
                             email=f"p{i}@example.com", minutes=i) for i in range(1, 21)]
    for copy_id, original in ((21, 2), (22, 9), (23, 17)):
        o = messages[original - 1]
        messages.append(make_message(copy_id, o.text, name=o.from_name, email=o.from_email, minutes=60 + copy_id))
    return messages


def _classes(messages):
    return {m.id: Classification(id=m.id, intent="application_status", confidence=0.9) for m in messages}


def _fake_llm(seen_prompts, delay_for=None):
    def fake(prompt, system=None):
        ids = [int(i) for i in IDS_IN_PROMPT.findall(prompt)]
        seen_prompts.append(ids)
        if delay_for:
            time.sleep(delay_for(ids))
        return json.dumps({"items": [{"id": i, "reply": f"Hi, reply for {i}.", "alt_reply": ""} for i in ids]})
    return fake


@pytest.mark.parametrize("batch_size", [1, 5, 10, 50])
def test_duplicates_never_reach_the_ai_whatever_the_batch_size(monkeypatch, batch_size):
    messages = _inbox()
    unique, duplicates = dedupe(messages)  # runs on the whole file before any batching
    assert duplicates == {21: 2, 22: 9, 23: 17}

    prompts = []
    monkeypatch.setattr(draft_module, "BATCH_SIZE", batch_size)
    monkeypatch.setattr(draft_module, "call_llm", _fake_llm(prompts))
    drafts = draft_module.draft_replies(unique, _classes(unique))

    sent = [i for ids in prompts for i in ids]
    assert sorted(drafts) == list(range(1, 21))          # every original drafted
    assert not set(duplicates) & set(sent)               # no copy ever sent to the AI
    assert sorted(sent) == list(range(1, 21))            # each original sent exactly once


def test_batches_of_five_finishing_out_of_order_still_map_each_reply_to_its_message(monkeypatch):
    unique, _ = dedupe(_inbox())
    prompts = []
    # First batch is slowest, so batches finish in reverse order.
    monkeypatch.setattr(draft_module, "call_llm", _fake_llm(prompts, delay_for=lambda ids: 0.05 if 1 in ids else 0))
    drafts = draft_module.draft_replies(unique, _classes(unique))

    assert all(len(ids) <= 5 for ids in prompts)
    assert len(prompts) == 4                              # 20 messages / 5
    assert all(drafts[i].reply == f"Hi, reply for {i}." for i in range(1, 21))


def test_batches_really_run_in_parallel(monkeypatch):
    unique, _ = dedupe(_inbox())
    active, peak, lock = [0], [0], threading.Lock()

    def slow(prompt, system=None):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        ids = [int(i) for i in IDS_IN_PROMPT.findall(prompt)]
        return json.dumps({"items": [{"id": i, "reply": "Hi.", "alt_reply": ""} for i in ids]})

    monkeypatch.setattr(draft_module, "call_llm", slow)
    draft_module.draft_replies(unique, _classes(unique))
    assert peak[0] == draft_module.PARALLEL_BATCHES


def test_draft_batches_yields_each_batch_as_it_finishes(monkeypatch):
    unique, _ = dedupe(_inbox())
    monkeypatch.setattr(draft_module, "call_llm", _fake_llm([]))
    ready, batches = draft_module.plan_drafts(unique, _classes(unique))
    chunks = list(draft_module.draft_batches(batches, _classes(unique)))
    assert ready == {} and len(chunks) == 4 and all(len(c) == 5 for c in chunks)


def test_gemini_call_turns_thinking_off_and_records_tokens(monkeypatch):
    types = pytest.importorskip("google.genai.types")
    captured = {}

    class Meta:
        prompt_token_count, candidates_token_count, thoughts_token_count = 700, 120, None

    class Response:
        text, usage_metadata = '{"items": []}', Meta()

    class Models:
        def generate_content(self, model, contents, config):
            captured["config"] = config
            return Response()

    class Client:
        models = Models()

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.delenv("GEMINI_THINKING_BUDGET", raising=False)
    monkeypatch.delenv("RECORD", raising=False)
    monkeypatch.setattr(llm, "_gemini_client", lambda: Client())

    llm.call_llm("hello", system="rules")
    assert isinstance(captured["config"].thinking_config, types.ThinkingConfig)
    assert captured["config"].thinking_config.thinking_budget == 0
    assert llm.get_usage() == [{"provider": "gemini", "seconds": llm.get_usage()[0]["seconds"],
                                "input_tokens": 700, "output_tokens": 120, "thinking_tokens": None}]
    assert llm.usage_summary()["calls"] == 1
