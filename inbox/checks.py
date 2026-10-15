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


def run_checks(reply: str, other_candidate_names: list[str]) -> list[str]:
    """Return flag labels for this reply, in a fixed order, each at most once."""
    flags = [label for label, pattern in FIXED_FLAGS if pattern.search(reply)]
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
