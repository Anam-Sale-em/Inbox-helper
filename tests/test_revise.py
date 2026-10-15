"""Revisions: recruiter feedback wins (directive v1.3). Guards against the Day 2 regression
where a rewritten draft.py brought back the old 'never break the rules' revision prompt."""

import json

from inbox import draft as draft_module
from inbox.draft import REVISE_SYSTEM_PROMPT, revise_reply
from tests.conftest import make_message


def test_revise_uses_its_own_prompt_where_recruiter_wins(monkeypatch):
    seen = {}

    def fake(prompt, system=None):
        seen["prompt"], seen["system"] = prompt, system
        return json.dumps({"items": [{"id": 1, "reply": "Hi Asha, your interview is confirmed for Friday at 3 PM."}]})

    monkeypatch.setattr(draft_module, "call_llm", fake)
    draft = revise_reply(make_message(1), "reschedule_interview", "old reply", "Confirm Friday 3pm is fine")

    assert seen["system"] is REVISE_SYSTEM_PROMPT
    assert "Never promise" not in seen["system"]          # first-draft rule must not leak in
    assert "Follow it exactly" in seen["system"]
    assert '<message id="1"' in seen["prompt"]              # candidate text still delimited
    assert draft.reply.startswith("Hi Asha, your interview is confirmed")
