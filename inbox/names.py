"""First name for greetings, from whatever the email client put in the display name."""

import re

_TAGS = re.compile(r"<[^>]*>")
_BRACKETS = re.compile(r"\([^)]*\)")
_TITLES = {"dr", "mr", "mrs", "ms", "miss", "mx", "prof", "er", "sir", "madam", "shri", "smt", "sri"}


def first_name(display_name: str) -> str:
    """"Dr. Priya Nair" → "Priya", "KUMAR, VARUN" → "Varun", "rahul verma" → "Rahul", "" → "there"."""
    name = _BRACKETS.sub(" ", _TAGS.sub("", display_name or ""))  # "<b>Meera</b>", "Neha (HR Team)"
    if "," in name:  # "Last, First"
        last, _, rest = name.partition(",")
        name = f"{rest} {last}" if rest.strip() else last
    words = [w.strip(".,") for w in name.split()]
    words = [w for w in words if w and w.lower() not in _TITLES]
    if not words:
        return "there"
    word = words[0]
    if word.isupper() or word.islower():  # fix ALL CAPS / all lower; leave "McKenzie" and non-Latin alone
        word = word[0].upper() + word[1:].lower()
    return word
