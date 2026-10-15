from inbox.review import review
from inbox.schema import Draft
from tests.conftest import make_message


def test_review_approve_reject_edit():
    first = make_message(1, email="asha@example.com")
    second = make_message(2, name="Ravi Kumar", email="ravi@example.com")
    third = make_message(3, name="Meera Shah", email="meera@example.com")
    groups = {"reschedule_interview": [first, second], "other": [third]}
    drafts = {
        1: Draft(id=1, reply="Hi Asha, I'll check with the team."),
        2: Draft(id=2, reply="Hi Ravi, thanks for writing."),
        3: Draft(id=3, reply="Hi Meera, noted."),
    }
    answers = iter(["a", "r", "e", "Hi Meera, thanks, I'll get back to you soon."])

    approved = review(groups, drafts, {}, input_fn=lambda prompt: next(answers))

    assert approved == [
        {"id": 1, "to": "asha@example.com", "reply": "Hi Asha, I'll check with the team.", "edited": False},
        {"id": 3, "to": "meera@example.com", "reply": "Hi Meera, thanks, I'll get back to you soon.", "edited": True},
    ]


def test_review_reasks_on_unknown_key():
    message = make_message(1)
    answers = iter(["x", "", "a"])

    approved = review(
        {"other": [message]}, {1: Draft(id=1, reply="Hi Asha.")}, {},
        input_fn=lambda prompt: next(answers),
    )

    assert [item["id"] for item in approved] == [1]
