"""Day 4: a reviewer can bring their own messages (upload JSON/CSV or paste), same rules as files."""

import json

from inbox.intake import load_pasted, load_upload


def test_csv_with_friendly_headers_and_missing_id_and_time():
    csv_text = ("name,email,message\n"
                "Riya Sharma,riya@example.com,Can we move my interview to Friday?\n"
                "Arjun Mehta,arjun@example.com,\"Any update, please?\"\n")
    report = load_upload("mine.csv", csv_text.encode("utf-8"))
    assert [(m.id, m.from_name, m.text) for m in report.messages] == [
        (1, "Riya Sharma", "Can we move my interview to Friday?"), (2, "Arjun Mehta", "Any update, please?")]
    assert all(m.received_at.utcoffset() is not None for m in report.messages)
    assert report.rejected == []


def test_csv_row_without_email_is_shown_not_dropped():
    report = load_upload("mine.csv", b"from_name,from_email,text\nA,,Hello\nB,b@example.com,Hi\n")
    assert [m.from_email for m in report.messages] == ["b@example.com"]
    assert report.rejected[0]["label"] == "Row 2" and "from_email" in report.rejected[0]["reason"]


def test_json_upload_works_like_a_file_and_fills_missing_ids():
    data = [{"from_name": "A", "from_email": "a@example.com", "text": "Hi"},
            {"id": 7, "from_name": "B", "from_email": "b@example.com", "text": "Hello",
             "received_at": "2026-10-17T10:00:00+05:30"}]
    report = load_upload("mine.json", json.dumps(data).encode("utf-8"))
    assert sorted(m.id for m in report.messages) == [7, 8]


def test_utf8_bom_from_excel_is_fine():
    report = load_upload("excel.csv", "﻿name,email,message\nA,a@example.com,Hi\n".encode("utf-8"))
    assert [m.from_name for m in report.messages] == ["A"]


def test_pasted_lines():
    text = ("Riya Sharma <riya@example.com>: Can we move my interview to Friday?\n"
            "\n"
            "arjun@example.com: Any update?\n"
            "this line has no sender\n")
    report = load_pasted(text)
    assert [(m.from_name, m.from_email) for m in report.messages] == [
        ("Riya Sharma", "riya@example.com"), ("", "arjun@example.com")]
    assert report.rejected[0]["label"] == "Line 4"
    assert "Name <email>: message" in report.rejected[0]["reason"]
