"""Free interview slots from the recruiter's Google Calendar, for reschedule offers.

Free/busy only (scope calendar.freebusy): no event titles, no guests. The AI never sees any of
it. The code lists free slots, the recruiter picks, and the picks fill the reply's
[add time slots] blank. Day 2 dropped a static slot list because it wasn't real; the calendar is.

Settings in .env (all optional): CAL_ID (default primary), CAL_DAYS_AHEAD (5 working days),
CAL_DAY_START / CAL_DAY_END (10 / 18, IST hours), CAL_SLOT_MINUTES (45), CAL_LEAD_HOURS (12).
"""

import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from inbox.intake import IST
from inbox.reschedule import SLOTS_TOKEN

_WEEKDAY = {0: r"mon(?:day)?", 1: r"tue(?:s|sday)?", 2: r"wed(?:nesday)?", 3: r"thu(?:rs?|rsday)?",
            4: r"fri(?:day)?", 5: r"sat(?:urday)?", 6: r"sun(?:day)?"}


class CalendarError(RuntimeError):
    """Google answered, but not with this calendar's free/busy (wrong CAL_ID, no access...)."""


@dataclass(frozen=True)
class SlotRules:
    days_ahead: int = 5     # working days after today
    day_start: int = 10     # first slot starts at this hour (IST)
    day_end: int = 18       # last slot ends by this hour
    minutes: int = 45       # interview length
    lead_hours: int = 12    # never offer a slot sooner than this
    calendar_id: str = "primary"
    per_day: int = 2        # suggestions per day
    limit: int = 5          # suggestions per message

    @classmethod
    def from_env(cls) -> "SlotRules":
        env = lambda key, default: int(os.getenv(key, default))  # noqa: E731
        return cls(days_ahead=env("CAL_DAYS_AHEAD", 5), day_start=env("CAL_DAY_START", 10),
                   day_end=env("CAL_DAY_END", 18), minutes=env("CAL_SLOT_MINUTES", 45),
                   lead_hours=env("CAL_LEAD_HOURS", 12), calendar_id=os.getenv("CAL_ID", "primary"))


def working_days(after: date, count: int) -> list[date]:
    days, day = [], after
    while len(days) < count:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days


def free_slots(busy: list[tuple[datetime, datetime]], now: datetime, rules: SlotRules) -> list[datetime]:
    """Every slot start (on the hour or half hour) where the whole interview fits between busy blocks."""
    earliest, length = now + timedelta(hours=rules.lead_hours), timedelta(minutes=rules.minutes)
    slots = []
    for day in working_days(now.astimezone(IST).date(), rules.days_ahead):
        start = datetime.combine(day, time(rules.day_start), IST)
        close = datetime.combine(day, time(rules.day_end), IST)
        while start + length <= close:
            end = start + length
            if start >= earliest and not any(start < b_end and b_start < end for b_start, b_end in busy):
                slots.append(start)
            start += timedelta(minutes=30)
    return slots


def asked_weekdays(text: str) -> set[int]:
    lowered = text.lower()
    return {day for day, pattern in _WEEKDAY.items() if re.search(rf"\b{pattern}\b", lowered)}


def suggest(slots: list[datetime], message_text: str, rules: SlotRules) -> list[datetime]:
    """A few slots to offer: the weekday the candidate asked for first, at most `per_day` a day,
    never overlapping each other."""
    asked = asked_weekdays(message_text)
    ordered = sorted(slots, key=lambda s: (s.weekday() not in asked, s))
    picks, per_day, length = [], Counter(), timedelta(minutes=rules.minutes)
    for slot in ordered:
        if per_day[slot.date()] >= rules.per_day:
            continue
        if any(p.date() == slot.date() and abs(p - slot) < length for p in picks):
            continue
        picks.append(slot)
        per_day[slot.date()] += 1
        if len(picks) == rules.limit:
            break
    return picks


def label(slot: datetime) -> str:
    """'Thu 22 Oct at 3:30 PM': readable, and the approve check sees a real time in it."""
    slot = slot.astimezone(IST)
    clock = slot.strftime("%I:%M %p").lstrip("0")
    return f"{slot.strftime('%a')} {slot.day} {slot.strftime('%b')} at {clock}"


def fill_blank(reply: str, labels: list[str]) -> str:
    """Put the picked times where the AI left the blank. No blank, no change."""
    if not labels or SLOTS_TOKEN not in reply:
        return reply
    times = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]
    return reply.replace(SLOTS_TOKEN, times, 1)


def get_free_slots(now: datetime | None = None, rules: SlotRules | None = None, service=None) -> list[datetime]:
    """Free slots over the next working days, from Google's free/busy for one calendar."""
    rules = rules or SlotRules.from_env()
    now = now or datetime.now(IST)
    if service is None:
        from inbox.google_auth import build_service
        service = build_service("calendar", "v3")
    last_day = working_days(now.astimezone(IST).date(), rules.days_ahead)[-1]
    body = {"timeMin": now.isoformat(),
            "timeMax": datetime.combine(last_day, time(23, 59), IST).isoformat(),
            "timeZone": "Asia/Kolkata", "items": [{"id": rules.calendar_id}]}
    answer = service.freebusy().query(body=body).execute()
    calendars = answer.get("calendars", {})
    # Asked for one calendar, so one answer: take it even if Google keys it by the address, not "primary".
    calendar = calendars.get(rules.calendar_id) or (next(iter(calendars.values())) if len(calendars) == 1 else {})
    if calendar.get("errors"):
        reasons = ", ".join(e.get("reason", "?") for e in calendar["errors"])
        raise CalendarError(f"Google Calendar couldn't read '{rules.calendar_id}': {reasons}")
    busy = [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in calendar.get("busy", [])]
    return free_slots(busy, now, rules)


if __name__ == "__main__":
    rules = SlotRules.from_env()
    found = get_free_slots(rules=rules)
    print(f"{len(found)} free {rules.minutes}-minute slots in the next {rules.days_ahead} working days. First few:")
    for slot in suggest(found, "", rules):
        print("  ", label(slot))
