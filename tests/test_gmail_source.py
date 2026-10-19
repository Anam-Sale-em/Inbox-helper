"""Gmail as a message source (19 Oct). Read-only, checked like n8n's Gmail trigger: each check
reads the emails matching a search and skips ones already loaded. No test talks to Google:
a fake service returns the same shapes the Gmail API does."""

import base64
from datetime import timedelta

import pytest

from inbox import gmail_source


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")  # Gmail drops the padding


def email(gmail_id, sender, text=None, html=None, subject="Interview", minute=0):
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
    ms = 1_792_386_000_000 + minute * 60_000  # 19 Oct 2026, 10:30 IST
    return {"id": gmail_id, "internalDate": str(ms),
            "payload": {"mimeType": "multipart/alternative", "parts": parts,
                        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]}}


class _Call:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGmail:
    """users().messages().list/get, newest first like the real API."""

    def __init__(self, emails):
        self.emails = {e["id"]: e for e in emails}
        self.queries = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        self.queries.append(q)
        newest_first = sorted(self.emails.values(), key=lambda e: -int(e["internalDate"]))
        return _Call({"messages": [{"id": e["id"]} for e in newest_first][:maxResults]})

    def get(self, userId, id, format):
        return _Call(self.emails[id])


@pytest.fixture(autouse=True)
def seen_file(tmp_path, monkeypatch):
    monkeypatch.setattr(gmail_source, "SEEN_FILE", tmp_path / "gmail_seen.json")


def test_emails_become_messages_through_the_same_loader():
    service = FakeGmail([
        email("g2", "arjun@example.com", text="Any update on my application?", minute=5),
        email("g1", "Riya Sharma <riya@example.com>", text="Can we move my interview to Friday?"),
    ])
    report = gmail_source.load_gmail("label:inbox-helper", service=service)
    assert [(m.id, m.from_name, m.from_email, m.text) for m in report.messages] == [
        (1, "Riya Sharma", "riya@example.com", "Can we move my interview to Friday?"),  # oldest first
        (2, "", "arjun@example.com", "Any update on my application?"),
    ]
    assert report.messages[0].received_at.utcoffset() == timedelta(hours=5, minutes=30)  # shown in IST
    assert report.source_ids == {1: "g1", 2: "g2"}
    assert service.queries == ["label:inbox-helper"]


def test_quoted_thread_and_signature_are_cut():
    body = ("Could we do Monday 11 am instead?\n\nThanks,\nNeeraj\n-- \nNeeraj Rao | +91 98xxxxxx\n\n"
            "On Tue, 13 Oct 2026 at 10:00, Recruiter Team <\nhr@example.com> wrote:\n> Could you do Thursday 4 pm?\n")
    report = gmail_source.load_gmail("q", service=FakeGmail([email("g1", "neeraj@example.com", text=body)]))
    assert report.messages[0].text == "Could we do Monday 11 am instead?\n\nThanks,\nNeeraj"


def test_html_only_email_is_read_as_plain_text():
    html_body = "<div>Hi,<br>Any update?</div><p>Thanks &amp; regards</p><style>p{color:red}</style>"
    report = gmail_source.load_gmail("q", service=FakeGmail([email("g1", "a@example.com", html=html_body)]))
    assert report.messages[0].text == "Hi,\nAny update?\nThanks & regards"


def test_plain_text_part_wins_over_html():
    service = FakeGmail([email("g1", "a@example.com", text="plain version", html="<b>html version</b>")])
    assert gmail_source.load_gmail("q", service=service).messages[0].text == "plain version"


def test_empty_body_falls_back_to_the_subject():
    service = FakeGmail([email("g1", "a@example.com", text="  ", subject="Reschedule request for Friday")])
    assert gmail_source.load_gmail("q", service=service).messages[0].text == "Reschedule request for Friday"


def test_only_new_emails_are_loaded_once_a_run_is_saved():
    service = FakeGmail([email("g1", "a@example.com", text="first"), email("g2", "b@example.com", text="second")])
    first = gmail_source.load_gmail("q", service=service)
    assert len(first.messages) == 2
    gmail_source.mark_seen(first.source_ids.values())  # done after the run folder is saved

    service.emails["g3"] = email("g3", "c@example.com", text="third", minute=9)
    assert gmail_source.count_new("q", service=service) == 1  # what the every-minute check shows
    assert gmail_source.count_new("q", service=service, exclude=["g3"]) == 0  # already on screen
    assert [m.text for m in gmail_source.load_gmail("q", service=service).messages] == ["third"]
    assert len(gmail_source.load_gmail("q", only_new=False, service=service).messages) == 3


def test_email_without_a_sender_is_listed_with_the_reason_not_dropped():
    report = gmail_source.load_gmail("q", service=FakeGmail([email("g1", "", text="hello")]))
    assert report.messages == [] and "from_email" in report.rejected[0]["reason"]


def test_reply_headers_from_gmail_and_outlook_are_cut_too():
    assert gmail_source.new_part("Monday works.\n\nOn Tue, 13 Oct 2026 at 10:00, HR <hr@example.com> wrote:\n"
                                 "> Thursday?") == "Monday works."
    assert gmail_source.new_part("Done, attached.\r\n\r\n-----Original Message-----\r\nFrom: HR\r\n") == "Done, attached."
    assert gmail_source.new_part("On Friday I can't make it. Monday?") == "On Friday I can't make it. Monday?"
