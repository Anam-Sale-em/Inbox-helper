"""Checks on the reviewer's "Request changes" comment before it is sent to the AI.

Catches the three ways a comment goes wrong in practice: too short to act on,
an approval typed in the wrong box ("ok", "looks good"), or sent half-written.
"""

import re

MIN_FEEDBACK_WORDS = 3

# The examples already sit in the tip under the comment box, so this doesn't repeat them.
EXAMPLE_ASK = "Could you say a little more about what you'd like changed?"

# Whole-comment approvals: these belong on the Approve button, not in a revision request.
APPROVAL_ONLY = {
    "ok", "okay", "k", "fine", "good", "great", "nice", "yes", "yep", "done", "approved",
    "approve", "lgtm", "looks good", "looks fine", "all good", "its fine", "it's fine",
    "good to go", "no changes", "nothing", "no change", "perfect", "ok fine", "fine by me",
}

# Comments that ask for a change without saying what (after dropping "please"/"pls").
TOO_VAGUE = {
    "change it", "change this", "fix it", "fix this", "redo it", "redo this", "redo",
    "rewrite it", "rewrite this", "rewrite", "improve it", "improve this", "make it better",
    "do it again", "try again", "change", "fix", "edit it", "update it", "not good",
    "not right", "wrong", "no",
}

# Keyboard mashing: a word with no vowel (4+ letters) or 6+ consonants in a row.
# Real words in feedback ("strengths", "CTC", "LPA", "1pm") stay under these limits.
_NO_VOWEL_WORD = re.compile(r"^[b-df-hj-np-tv-xz]{4,}$", re.IGNORECASE)
_LONG_CONSONANT_RUN = re.compile(r"[b-df-hj-np-tv-xz]{6,}", re.IGNORECASE)


# Whole-comment vowel share. Measured on real comments (English, Hinglish, acronym-heavy):
# all 0.32+. Keyboard mashes: 0.21 or less. 0.25 sits in the gap.
MIN_VOWEL_SHARE = 0.25
# Short forms recruiters type that have few or no vowels; left out of the vowel count.
VOWELLESS_OK = {"pls", "plz", "hrs", "hr", "pm", "am", "hmm", "msg", "mtg", "jd", "cv", "wfh", "thx", "tbh"}


def _vowel_share(words: list[str]) -> tuple[float, int]:
    kept = [w for w in words if w.isalpha()
            and not (len(w) >= 2 and w.isupper())          # acronyms: CTC, LPA, HR
            and w.lower() not in VOWELLESS_OK]
    letters = "".join(kept).lower()
    if not letters:
        return 1.0, 0
    return sum(c in "aeiouy" for c in letters) / len(letters), len(letters)


def _looks_like_gibberish(words: list[str]) -> bool:
    letters_only = [w for w in words if w.isalpha()]
    if not letters_only:
        return False
    odd = [w for w in letters_only if _NO_VOWEL_WORD.match(w) or _LONG_CONSONANT_RUN.search(w)]
    if len(odd) * 2 >= len(letters_only):  # half or more of the words look mashed
        return True
    share, letter_count = _vowel_share(words)
    return letter_count >= 6 and share < MIN_VOWEL_SHARE


# A comment ending on one of these was most likely sent mid-sentence.
DANGLING_ENDINGS = {
    "and", "or", "but", "to", "with", "for", "the", "a", "an", "of", "in", "on", "at",
    "that", "if", "because", "so", "then", "about", "by", "from", "into", "than",
}


_FILLER = re.compile(r"^(?:this|it|that|that's|thats|it's|its)\s+|\s+(?:to me|for me|thanks|thank you|thx|now|then)$")
_MORE_APPROVALS = {"looks great", "sounds good", "good enough", "all set", "send it", "ship it",
                   "all set send it", "is good", "looks okay", "looks ok"}


def _approval_only(normalized: str) -> bool:
    """"looks good to me", "this looks fine", "LGTM thanks": approval with filler, nothing to change."""
    text = normalized
    for _ in range(3):  # peel "this … to me thanks" layer by layer
        text = _FILLER.sub("", text).strip()
    return normalized in APPROVAL_ONLY or text in APPROVAL_ONLY or text in _MORE_APPROVALS


def feedback_problem(comment: str) -> str | None:
    """Return a message explaining what's wrong with the comment, or None if it's usable."""
    text = comment.strip()
    if not text:
        return "Please add a short note on what you'd like changed."

    normalized = re.sub(r"[^\w\s']", "", text.lower()).strip()
    if _approval_only(normalized):
        return "Looks like you're happy with this draft. You can use Approve instead."

    without_please = re.sub(r"\b(?:please|pls|plz|kindly)\b", "", normalized)
    without_please = re.sub(r"\s+", " ", without_please).strip()
    if without_please in TOO_VAGUE:
        return EXAMPLE_ASK

    words = re.findall(r"[\w'’]+", text)
    if _looks_like_gibberish(words):
        return "This doesn't look like a readable comment. Could you rephrase what you'd like changed?"

    if len(words) < MIN_FEEDBACK_WORDS:
        return EXAMPLE_ASK

    if text[-1].isalnum() and words[-1].lower() in DANGLING_ENDINGS:
        return (f"It looks like this comment was cut off (it ends with \"{words[-1]}\"). "
                "Please finish it and try again.")

    return None
