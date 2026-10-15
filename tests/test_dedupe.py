from inbox.dedupe import dedupe
from tests.conftest import make_message

TEXT = "Hi, can we please move my interview to Friday afternoon? Something came up."


def test_dedupe_exact_near_and_different_sender():
    original = make_message(1, TEXT, minutes=0)
    exact_resend = make_message(2, "  hi CAN we please move my interview to friday afternoon something came up!!  ", minutes=5)
    near_duplicate = make_message(3, TEXT.replace("Something came up.", "Something just came up."), minutes=10)
    other_sender = make_message(4, TEXT, name="Ravi Kumar", email="ravi@example.com", minutes=15)

    # Shuffled on purpose: dedupe must sort by received_at itself.
    unique, duplicates = dedupe([near_duplicate, other_sender, exact_resend, original])

    assert duplicates == {2: 1, 3: 1}
    assert [m.id for m in unique] == [1, 4]


def test_dedupe_keeps_earliest_even_when_listed_last():
    later = make_message(10, TEXT, minutes=30)
    earlier = make_message(11, TEXT, minutes=0)

    unique, duplicates = dedupe([later, earlier])

    assert [m.id for m in unique] == [11]
    assert duplicates == {10: 11}
