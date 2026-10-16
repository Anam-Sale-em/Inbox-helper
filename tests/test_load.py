import json
from datetime import datetime

from inbox.dedupe import dedupe
from inbox.schema import Message
from main import load_report, split_empty


def _write(tmp_path, items):
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def _msg(msg_id, email, text="Any update?", at="2026-10-15T10:00:00+05:30"):
    return {"id": msg_id, "from_name": "A B", "from_email": email, "received_at": at, "text": text}


def test_bad_records_are_shown_with_a_reason_and_the_rest_still_load(tmp_path):
    # 16 Oct: one broken record used to reject the whole file, so nobody got a reply.
    bad_email = {"id": 2, "from_name": "Missing Email", "received_at": "2026-10-15T10:01:00+05:30", "text": "Hi"}
    bad_id = _msg("three", "c@example.com", at="yesterday")
    report = load_report(_write(tmp_path, [_msg(1, "a@example.com"), bad_email, bad_id]))

    assert [m.id for m in report.messages] == [1]
    assert [r["position"] for r in report.rejected] == [2, 3]
    assert "from_email" in report.rejected[0]["reason"]
    assert report.rejected[0]["record"] == bad_email  # the reviewer sees the raw record as proof


def test_repeated_id_gets_a_new_id_and_a_note_instead_of_blocking_the_file(tmp_path):
    # 16 Oct: two different candidates shared id 4. Both need a reply; replies go to the email,
    # so the later one gets the next free id and the change is shown, never silent.
    items = [_msg(4, "one@example.com", "Can we reschedule?"),
             _msg(4, "two@example.com", "What is the salary range?"),
             _msg(5, "three@example.com")]
    report = load_report(_write(tmp_path, items))

    assert [(m.id, m.from_email) for m in report.messages] == [
        (4, "one@example.com"), (6, "two@example.com"), (5, "three@example.com")]
    assert report.renumbered == [{"old_id": 4, "new_id": 6, "from_email": "two@example.com",
                                  "kept_by": "one@example.com"}]


def test_time_without_timezone_is_treated_as_ist_so_dedupe_does_not_crash(tmp_path):
    # 16 Oct: data/edge_timezones.json crashed dedupe (can't compare times with and without a zone).
    items = [_msg(1, "a@example.com"), _msg(2, "b@example.com", at="2026-10-15T10:05:00")]
    report = load_report(_write(tmp_path, items))

    assert all(m.received_at.utcoffset() is not None for m in report.messages)
    assert report.messages[1].received_at.isoformat() == "2026-10-15T10:05:00+05:30"
    assert report.no_timezone == [2]
    dedupe(report.messages)  # no TypeError


def test_empty_messages_are_skipped_before_any_ai_call():
    # 16 Oct, data/edge_text.json: empty and spaces-only messages got a holding reply.
    def m(i, text):
        return Message(id=i, from_name="A B", from_email=f"{i}@example.com",
                       received_at=datetime(2026, 10, 15, 10, 0), text=text)

    kept, empty = split_empty([m(1, "Any update?"), m(2, ""), m(3, "  \n\t "), m(4, "https://x.example")])
    assert [x.id for x in kept] == [1, 4]
    assert [x.id for x in empty] == [2, 3]
