"""Report-only safety checks on draft replies. Flags problems; never blocks or edits."""

import re

# --- building blocks ---------------------------------------------------------

NUMBER = r"\d[\d,]*(?:\.\d+)?"
WEEKDAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|today|tomorrow"
MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
DAY_OF_MONTH = r"\d{1,2}(?:st|nd|rd|th)?"
DATE = (
    rf"{DAY_OF_MONTH}\s+(?:{MONTH})"          # 12 Oct, 3rd November
    rf"|(?:{MONTH})\s+{DAY_OF_MONTH}"         # Oct 12, November 3rd
    r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"    # 12/10, 12-10-2026
)
DAY_OR_DATE = rf"\b(?:{WEEKDAY}|{DATE})\b"
CONFIRM = r"\bconfirm\w*"
NEAR = 40  # max characters between "confirm" and a day/date
YOU_ARE = r"\byou(?:['’]?re| are)"

# --- flag patterns -----------------------------------------------------------

SALARY = re.compile(
    rf"{NUMBER}\s*(?:lpa|lakhs?|lacs?|k)\b"       # 12 LPA, 8.5 lakh, 50k
    rf"|(?:₹|\brs\.?|\binr)\s*{NUMBER}"          # ₹50,000, Rs. 40000, INR 9
    rf"|\bctc\b\D{{0,20}}{NUMBER}"               # CTC of 12
    rf"|{NUMBER}\D{{0,20}}\bctc\b",              # 12 ... CTC
    re.IGNORECASE,
)

DATE_COMMITMENT = re.compile(
    rf"\bconfirmed for\b"
    rf"|{CONFIRM}.{{0,{NEAR}}}{DAY_OR_DATE}"
    rf"|{DAY_OR_DATE}.{{0,{NEAR}}}{CONFIRM}",
    re.IGNORECASE | re.DOTALL,
)

OFFER_OR_OUTCOME = re.compile(
    rf"{YOU_ARE} selected\b"
    rf"|\boffer (?:is|has been) confirmed\b"
    rf"|\bcleared the (?:\w+ )?round\b"
    rf"|{YOU_ARE} hired\b"
    rf"|\bcongratulations on (?:the|your) offer\b",
    re.IGNORECASE,
)

FIXED_FLAGS: list[tuple[str, re.Pattern[str]]] = [
    ("salary", SALARY),
    ("date_commitment", DATE_COMMITMENT),
    ("offer_or_outcome", OFFER_OR_OUTCOME),
]


# Clock times: "3 PM", "3:00pm", "11 am", "15:00".
CLOCK_TIME = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b\.?"   # 12-hour with am/pm
    r"|\b([01]?\d|2[0-3]):([0-5]\d)\b",                    # 24-hour
    re.IGNORECASE,
)


def times_in(text: str) -> list[tuple[int, str]]:
    """Every clock time in the text as (minutes after midnight, the words that matched)."""
    found = []
    for m in CLOCK_TIME.finditer(text):
        if m.group(3):
            hour, minute = int(m.group(1)) % 12, int(m.group(2) or 0)
            if m.group(3).lower() == "p":
                hour += 12
        else:
            hour, minute = int(m.group(4)), int(m.group(5))
        found.append((hour * 60 + minute, m.group(0).strip()))
    return found


def unlisted_times(reply: str, allowed_slots: list[str]) -> list[str]:
    """Times in the reply that don't match any of the recruiter's slots."""
    allowed = {minutes for slot in allowed_slots for minutes, _ in times_in(slot)}
    return [words for minutes, words in times_in(reply) if minutes not in allowed]


def run_checks(
    reply: str,
    other_candidate_names: list[str],
    allowed_slots: list[str] | None = None,
) -> list[str]:
    """Return flag labels for this reply, in a fixed order, each at most once.

    allowed_slots=None skips the slot check (no facts given); an empty list means
    no times are allowed, so any time in the reply is flagged.
    """
    flags = [label for label, pattern in FIXED_FLAGS if pattern.search(reply)]
    if allowed_slots is not None and unlisted_times(reply, allowed_slots):
        flags.append("unlisted_time")
    if _mentions_any(reply, other_candidate_names):
        flags.append("other_candidate")
    return flags


def _mentions_any(reply: str, names: list[str]) -> bool:
    for name in names:
        name = name.strip()
        if not name:
            continue
        # Lookarounds instead of \b so names with dots or hyphens still match whole.
        pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
        if re.search(pattern, reply, re.IGNORECASE):
            return True
    return False
