"""The Request changes comment must be usable before it reaches the AI."""

import pytest

from inbox.feedback import feedback_problem


@pytest.mark.parametrize("comment", [
    "Make it shorter",                         # exactly 3 words
    "Offer 1pm or 3pm",
    "Don't mention Friday, ask for her slots",
    "Ask her for two alternative time slots",
    "More formal please",
    "Remove the salary figure.",
    "Make it warmer and less formal",
    "Ask what time works for her",
    "Mention the strengths of her profile",
    "Add the CTC of 18 LPA",
    "Offer slots at 11am, 2pm or 4pm",
    "Make it sound warmer pls",
    "isko thoda chhota karo",                  # Hinglish
    "Add CTC and LPA details",                 # acronyms
    "Say we can't do Thursday",
])
def test_real_comments_pass(comment):
    assert feedback_problem(comment) is None


@pytest.mark.parametrize("comment, reason", [
    ("", "short note"),
    ("   ", "short note"),
    ("ok", "Approve instead"),
    ("Looks good!", "Approve instead"),
    ("LGTM", "Approve instead"),
    ("fine", "Approve instead"),
    ("shorter", "say a little more"),
    ("More formal", "say a little more"),
    ("change it", "say a little more"),
    ("Change it please", "say a little more"),
    ("pls fix this", "say a little more"),
    ("Make it better", "say a little more"),
    ("Make it shorter and", "cut off"),
    ("Ask her what time works for", "cut off"),
    ("Mention that we will reply to", "cut off"),
    ("ef,nroirentbkmdb dfvjn jdnvdb", "readable"),
    ("hjkl sdfg please", "readable"),
    ("kdn vfkn vejbv", "readable"),
    ("asdfgh qwerty zxcvbn", "readable"),
    ("sdfsdf sdf sdf", "readable"),
])
def test_bad_comments_are_blocked(comment, reason):
    problem = feedback_problem(comment)
    assert problem is not None and reason in problem


def test_messages_stay_polite():
    for comment in ["", "ok", "shorter", "change it", "Make it shorter and"]:
        message = feedback_problem(comment)
        assert not any(word in message for word in ("not just", "must", "Write what", "invalid"))


def test_known_limit_vowel_heavy_mash_slips_through():
    # Documented limit: a mash made mostly of vowel keys ("qwe rty uio") looks like text.
    # If it gets through, the AI returns the draft unchanged and the UI says so.
    assert feedback_problem("qwe rty uio") is None
