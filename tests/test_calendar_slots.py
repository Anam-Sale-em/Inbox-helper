"""Free interview slots from Google Calendar (19 Oct). Free/busy only; the AI never sees it.
The code lists free slots, the recruiter picks, and the picks fill the [add time slots] blank.
No test talks to Google: a fake service returns the free/busy API's shape."""

from datetime import datetime, timedelta, timezone

import pytest

from inbox import calendar_slots as cal
from inbox.checks import times_in
from inbox.reschedule import SLOTS_TOKEN, approval_blocker

IST = timezone(timedelta(hours=5, minutes=30))
MONDAY_5PM = datetime(2026, 10, 19, 17, 0, tzinfo=IST)
RULES = cal.SlotRules(days_ahead=3, day_start=10, day_end=13, minutes=45, lead_hours=12)


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


def test_free_slots_skip_busy_time_and_stay_in_working_hours():
    busy = [(at(20, 10, 30), at(20, 11, 30))]  # Tuesday 10:30-11:30
    slots = cal.free_slots(busy, MONDAY_5PM, RULES)
    assert [s for s in slots if s.day == 20] == [at(20, 11, 30), at(20, 12)]  # 45 min must fit before 13:00
    assert [s for s in slots if s.day == 21] == [at(21, 10), at(21, 10, 30), at(21, 11), at(21, 11, 30), at(21, 12)]
    assert {s.day for s in slots} == {20, 21, 22}  # Tue-Thu: the next 3 working days


def test_weekends_are_skipped_and_nothing_is_offered_too_soon():
    friday_9am = datetime(2026, 10, 23, 9, 0, tzinfo=IST)
    days = {s.date().isoformat() for s in cal.free_slots([], friday_9am, RULES)}
    assert days == {"2026-10-26", "2026-10-27", "2026-10-28"}  # Mon-Wed; not the same day, no weekend


def test_the_day_the_candidate_asked_for_comes_first_without_overlaps():
    slots = cal.free_slots([], MONDAY_5PM, RULES)
    picks = cal.suggest(slots, "Can we move my interview to Thursday? Something came up.", RULES)
    assert picks[:2] == [at(22, 10), at(22, 11)]  # Thursday first, at most 2, never overlapping
    assert len(picks) == 5 and all(p.day in (20, 21, 22) for p in picks)
    assert cal.suggest(slots, "Can we move the interview? Any time works.", RULES)[0] == at(20, 10)


def test_picked_slots_fill_the_blank_and_unlock_approve():
    reply = f"Hi Riya, we can move your interview. These slots are available: {SLOTS_TOKEN}. Please pick one."
    labels = [cal.label(at(22, 10)), cal.label(at(22, 15, 30))]
    assert labels == ["Thu 22 Oct at 10:00 AM", "Thu 22 Oct at 3:30 PM"]
    filled = cal.fill_blank(reply, labels)
    assert "available: Thu 22 Oct at 10:00 AM or Thu 22 Oct at 3:30 PM. Please" in filled
    assert approval_blocker(filled, offering_slots=True) is None and len(times_in(filled)) == 2
    assert cal.fill_blank(reply, ["A", "B", "C"]).count("A, B or C") == 1
    assert cal.fill_blank("No blank here.", labels) == "No blank here."


class FakeCalendar:
    def __init__(self, response):
        self.response, self.bodies = response, []

    def freebusy(self):
        return self

    def query(self, body):
        self.bodies.append(body)
        return self

    def execute(self):
        return self.response


def test_only_free_busy_is_asked_for_and_utc_times_come_back_as_ist():
    service = FakeCalendar({"calendars": {"primary": {"busy": [
        {"start": "2026-10-20T05:00:00Z", "end": "2026-10-20T06:00:00Z"}]}}})
    slots = cal.get_free_slots(now=MONDAY_5PM, rules=RULES, service=service)
    body = service.bodies[0]
    assert body["items"] == [{"id": "primary"}] and body["timeZone"] == "Asia/Kolkata"
    assert at(20, 10, 30) not in slots and at(20, 11, 30) in slots  # 05:00-06:00 UTC = 10:30-11:30 IST


def test_a_calendar_error_is_reported_not_silently_empty():
    service = FakeCalendar({"calendars": {"primary": {"errors": [{"reason": "notFound"}], "busy": []}}})
    with pytest.raises(cal.CalendarError, match="notFound"):
        cal.get_free_slots(now=MONDAY_5PM, rules=RULES, service=service)


def test_the_answer_is_used_even_when_google_keys_it_by_the_address():
    service = FakeCalendar({"calendars": {"anam@example.com": {"busy": [
        {"start": "2026-10-20T05:00:00Z", "end": "2026-10-20T06:00:00Z"}]}}})
    slots = cal.get_free_slots(now=MONDAY_5PM, rules=RULES, service=service)
    assert at(20, 10, 30) not in slots and at(20, 11, 30) in slots
