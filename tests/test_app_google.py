"""Review screen with Gmail and Google Calendar (19 Oct). Google itself is faked: no test signs in."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

import main
from inbox import calendar_slots, classify, draft, gmail_source
from inbox.intake import load_records

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))


def _start(tmp_path, monkeypatch, suffixes=None):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(main, "make_run_dir",
                        lambda suffix="": (suffixes.append(suffix) if suffixes is not None else None) or run_dir)
    return run_dir, AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()


def test_calendar_slots_fill_the_reschedule_blank_and_unlock_approve(tmp_path, monkeypatch):
    friday = [datetime(2026, 10, 23, 11, 0, tzinfo=IST), datetime(2026, 10, 23, 15, 0, tzinfo=IST)]
    reads = []
    monkeypatch.setattr(calendar_slots, "get_free_slots", lambda **_: reads.append(1) or friday)
    _, at = _start(tmp_path, monkeypatch)
    at.sidebar.selectbox[0].set_value(str(Path("data") / "messages.json")).run()
    at.toggle(key="use_calendar").set_value(True).run()
    [b for b in at.sidebar.button if b.label == "Run pipeline"][0].click().run()
    assert not at.exception

    # Riya (#1) asked for Friday. Her offer still has the blank, so Approve is off.
    assert at.button(key="approve_1_1").disabled
    at.session_state["slots_1_1"] = ["Fri 23 Oct at 11:00 AM"]  # what clicking that time does
    at.run()
    at.button(key="fill_1_1").click().run()
    reply = at.text_area(key="draft_1_1").value
    assert "Fri 23 Oct at 11:00 AM" in reply and "[add time slots]" not in reply
    assert not at.button(key="approve_1_1").disabled
    assert len(reads) == 1  # one free/busy read per run, not one per card


_MSG = re.compile(r'<message id="(\d+)"([^>]*)>')


def _fake_model(prompt, system=None):
    items = []
    for msg_id, attrs in _MSG.findall(prompt):
        if 'intent="' in attrs:  # drafting
            items.append({"id": int(msg_id), "alt_reply": "",
                          "reply": "Hi Riya, thanks for your message. We share an update within 5 working days."})
        else:  # sorting
            items.append({"id": int(msg_id), "intent": "application_status", "confidence": 0.9})
    return json.dumps({"items": items})


def test_gmail_run_is_saved_apart_and_emails_count_as_loaded_only_after_saving(tmp_path, monkeypatch):
    def fake_load(query=None, only_new=True, **_):
        report = load_records([{"id": 1, "from_name": "Riya Sharma", "from_email": "riya@example.com",
                                "received_at": "2026-10-19T10:30:00+05:30", "text": "Any update on my application?"}])
        report.source_ids = {1: "gmail-abc"}
        return report

    seen, suffixes = [], []
    monkeypatch.setattr(gmail_source, "load_gmail", fake_load)
    monkeypatch.setattr(gmail_source, "mark_seen", lambda ids: seen.extend(ids))
    monkeypatch.setattr(classify, "call_llm", _fake_model)
    monkeypatch.setattr(draft, "call_llm", _fake_model)
    run_dir, at = _start(tmp_path, monkeypatch, suffixes)

    at.radio(key="source").set_value("Gmail").run()
    at.toggle(key="mock").set_value(False).run()
    [b for b in at.sidebar.button if b.label == "Run pipeline"][0].click().run()
    assert not at.exception
    assert suffixes == ["_gmail"]  # runs/*_gmail is git-ignored: real email never reaches GitHub
    assert seen == ["gmail-abc"] and (run_dir / "drafts.json").exists()
