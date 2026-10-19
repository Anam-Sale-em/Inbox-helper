"""Candidate emails from Gmail, read-only, through the same loader as files.

Works like n8n's Gmail trigger: each check reads the emails matching a Gmail search
(GMAIL_QUERY in .env, default `label:inbox-helper newer_than:14d`) and skips the ones already
loaded. A read-only app can't mark mail as read, so loaded ids are kept in .gmail_seen.json
(git-ignored), and only after a run is saved, so a failed run doesn't lose anyone's email.
A Gmail filter that puts candidate mail under the label is the "trigger" in practice.
"""

import base64
import html
import json
import os
import re
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Iterable

from inbox.intake import IST, LoadReport, load_records

DEFAULT_QUERY = "label:inbox-helper newer_than:14d"
SEEN_FILE = Path(".gmail_seen.json")
MAX_PER_CHECK = 50

# Where the new part of a reply ends: Gmail's quote header (it can wrap onto a second line),
# Outlook's divider, or the standard "-- " signature line.
_CUT = re.compile(r"^On [^\n]{0,200}(?:\n[^\n]{0,120})?wrote:[ \t]*$"
                  r"|^-{2,}\s*Original Message\s*-{2,}"
                  r"|^-- $", re.M)


def default_query() -> str:
    return os.getenv("GMAIL_QUERY", DEFAULT_QUERY)


def load_gmail(query: str | None = None, only_new: bool = True, service=None,
               limit: int = MAX_PER_CHECK, interactive: bool = False) -> LoadReport:
    """Emails matching the search, oldest first, as messages. Same rules as every other source.
    interactive: allow the browser sign-in (terminal runs); the review screen never blocks on it."""
    service = service or _gmail(interactive)
    seen = seen_ids() if only_new else set()
    refs = [r["id"] for r in _list(service, query or default_query(), limit) if r["id"] not in seen]
    emails = [service.users().messages().get(userId="me", id=gid, format="full").execute() for gid in refs]
    emails.sort(key=lambda e: int(e.get("internalDate", 0)))
    records = [dict(_record(e), id=i) for i, e in enumerate(emails, 1)]
    report = load_records(records, label="Email {}", fill_defaults=True)
    report.source_ids = {i: e["id"] for i, e in enumerate(emails, 1)}
    return report


def count_new(query: str | None = None, service=None, limit: int = MAX_PER_CHECK,
              exclude: Iterable[str] = ()) -> int:
    """How many matching emails haven't been loaded yet: the every-minute check.
    exclude: ids already on screen in a run that isn't saved yet."""
    seen = seen_ids() | set(exclude)
    return sum(r["id"] not in seen for r in _list(service or _gmail(), query or default_query(), limit))


def seen_ids() -> set[str]:
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def mark_seen(gmail_ids: Iterable[str]) -> None:
    """Call after the run is saved, so a failed run doesn't skip anyone next time."""
    ids = seen_ids() | set(gmail_ids)
    SEEN_FILE.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def _gmail(interactive: bool = False):
    from inbox.google_auth import build_service
    return build_service("gmail", "v1", interactive=interactive)


def _list(service, query: str, limit: int) -> list[dict]:
    response = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
    return response.get("messages", [])


def _record(email: dict) -> dict:
    payload = email.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    name, address = parseaddr(headers.get("from", ""))
    received = datetime.fromtimestamp(int(email.get("internalDate", 0)) / 1000, IST)
    text = new_part(_body(payload)) or headers.get("subject", "").strip()
    return {"from_name": name.strip(), "from_email": address.strip(),
            "received_at": received.isoformat(timespec="seconds"), "text": text}


def _body(payload: dict) -> str:
    """The plain-text part if there is one, else the HTML part as text. Attachments are skipped."""
    plain, rich = [], []

    def walk(part: dict) -> None:
        data = part.get("body", {}).get("data")
        if data and not part.get("filename"):
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            if part.get("mimeType") == "text/plain":
                plain.append(text)
            elif part.get("mimeType") == "text/html":
                rich.append(text)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(plain) if plain else _html_to_text("\n".join(rich))


def _html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6])>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def new_part(text: str) -> str:
    """What the candidate wrote this time: no quoted thread, no signature, tidy blank lines."""
    text = text.replace("\r\n", "\n")
    cut = _CUT.search(text)
    if cut:
        text = text[:cut.start()]
    lines = [line.rstrip() for line in text.split("\n") if not line.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


if __name__ == "__main__":
    found = load_gmail(only_new=False)
    print(f"{len(found.messages)} email(s) match `{default_query()}`")
    for m in found.messages:
        print(f"  #{m.id} {m.from_name or m.from_email}: {m.text[:70]!r}")
