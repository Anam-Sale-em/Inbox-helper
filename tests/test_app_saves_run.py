"""The review screen saves its runs, same files as the terminal (19 Oct).

Before this it saved only the outbox, and only after an approval and a button click. Live runs
reviewed on screen left nothing behind for eval/day1_report.py or eval/judge.py to read.
This is also the first automated test that drives app.py itself (Streamlit's AppTest, mock mode).
"""

import importlib.util
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

import main

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("judge", ROOT / "eval" / "judge.py")
judge_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge_mod)


def test_review_screen_saves_the_run_and_every_decision(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(main, "make_run_dir", lambda suffix="": run_dir)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.sidebar.selectbox[0].set_value("data/messages.json").run()
    [b for b in at.sidebar.button if b.label == "Run pipeline"][0].click().run()
    assert not at.exception

    # The pipeline output is saved as soon as sorting and drafting finish, before any decision.
    for name in ("input_messages", "classifications", "drafts", "flags", "duplicates",
                 "skipped_no_reply", "ai_calls"):
        assert (run_dir / f"{name}.json").exists(), name
    assert len(judge_mod.load_items(run_dir)) == 76  # the judge reads a review-screen run

    # Decisions are saved as you make them: no button to remember.
    at.button(key="approve_1_11").click().run()
    at.button(key="hold_1_14").click().run()
    outbox = json.loads((run_dir / "outbox.json").read_text(encoding="utf-8"))
    on_hold = json.loads((run_dir / "on_hold.json").read_text(encoding="utf-8"))
    assert [(i["id"], i["edited"]) for i in outbox] == [(11, False)]
    assert [h["id"] for h in on_hold] == [14]
