"""Turn a file, an upload or pasted text into messages. One bad record never blocks the rest:
it's set aside with the reason, so the reviewer can see why."""

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from inbox.schema import Message

IST = timezone(timedelta(hours=5, minutes=30))  # times without a zone are read as IST

# Friendly column names people actually use in a sheet.
_ALIASES = {
    "name": "from_name", "sender": "from_name", "candidate": "from_name", "from_name": "from_name",
    "email": "from_email", "e-mail": "from_email", "from_email": "from_email",
    "message": "text", "body": "text", "text": "text",
    "date": "received_at", "time": "received_at", "timestamp": "received_at",
    "received": "received_at", "received_at": "received_at", "id": "id",
}
_PASTED = re.compile(r"^\s*(?:(?P<name>[^<:]*?)\s*<(?P<email>[^<>\s]+@[^<>\s]+)>|(?P<bare>[^\s:<>]+@[^\s:<>]+))"
                     r"\s*:\s*(?P<text>.+?)\s*$")


@dataclass
class LoadReport:
    """Messages to process, plus everything the reviewer should see."""
    messages: list[Message] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)    # {position, label, record, reason}
    renumbered: list[dict] = field(default_factory=list)  # {old_id, new_id, from_email, kept_by}
    no_timezone: list[int] = field(default_factory=list)  # ids read as IST
    source_ids: dict[int, str] = field(default_factory=dict)  # message id -> Gmail id (Gmail source only)


def load_file(path: str) -> LoadReport:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("not a list")
    return load_records(data, label="Record {} in the file")


def load_upload(filename: str, raw: bytes) -> LoadReport:
    """An uploaded .json or .csv. Missing ids and times are filled in (you can't expect them in a sheet)."""
    text = raw.decode("utf-8-sig")  # Excel adds a BOM
    if filename.lower().endswith(".csv"):
        rows = [{_ALIASES.get(k.strip().lower(), k.strip()): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(io.StringIO(text))]
        return load_records(rows, label="Row {}", fill_defaults=True, first_row=2)  # matches Excel: header is row 1
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("not a list")
    return load_records(data, label="Record {} in the file", fill_defaults=True)


def load_pasted(text: str) -> LoadReport:
    """One message per line: `Name <email>: message` or `email: message`."""
    records, bad = [], []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = _PASTED.match(line)
        if not match:
            bad.append({"position": line_no, "label": f"Line {line_no}", "record": {"line": line},
                        "reason": "Couldn't find a sender. Use: Name <email>: message"})
            continue
        records.append({"from_name": (match["name"] or "").strip(),
                        "from_email": match["email"] or match["bare"], "text": match["text"]})
    report = load_records(records, label="Line {}", fill_defaults=True)
    report.rejected = bad + report.rejected
    return report


def load_records(data: list, label: str = "Record {}", fill_defaults: bool = False,
                 first_row: int = 1) -> LoadReport:
    if fill_defaults:
        data = _fill_defaults(data)
    report, valid = LoadReport(), []
    for position, record in enumerate(data, 1):
        try:
            valid.append(Message.model_validate(record))
        except ValidationError as exc:
            report.rejected.append({"position": position, "label": label.format(position + first_row - 1),
                                    "record": record, "reason": _reason(exc)})

    # Two different messages with one id: keep the first, give the later one the next free id.
    # Replies go to the email address, so this is safe, and the note keeps it visible.
    next_id = max((m.id for m in valid), default=0) + 1
    owner: dict[int, str] = {}
    for i, m in enumerate(valid):
        if m.id in owner:
            report.renumbered.append({"old_id": m.id, "new_id": next_id, "from_email": m.from_email,
                                      "kept_by": owner[m.id]})
            m = valid[i] = m.model_copy(update={"id": next_id})
            next_id += 1
        owner[m.id] = m.from_email
        if m.received_at.tzinfo is None:
            valid[i] = m.model_copy(update={"received_at": m.received_at.replace(tzinfo=IST)})
            report.no_timezone.append(m.id)

    report.messages = valid
    return report


def _fill_defaults(data: list) -> list:
    """Uploads and pastes: number messages without an id, stamp missing times with now (IST)."""
    out = [dict(r) if isinstance(r, dict) else r for r in data]
    taken = {int(r["id"]) for r in out if isinstance(r, dict) and str(r.get("id", "")).strip().isdigit()}
    next_id = max(taken, default=0) + 1
    now = datetime.now(IST).isoformat(timespec="seconds")
    for r in out:
        if not isinstance(r, dict):
            continue
        if not str(r.get("id", "")).strip():
            r["id"] = next_id
            next_id += 1
        if not str(r.get("received_at", "")).strip():
            r["received_at"] = now
        if r.get("from_email") == "":
            r.pop("from_email")  # so the reason says "required", not "invalid"
    return out


def _reason(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}" for e in exc.errors())
