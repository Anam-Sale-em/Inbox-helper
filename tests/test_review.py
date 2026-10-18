from inbox.review import review
from inbox.schema import Draft
from tests.conftest import make_message


def test_review_approve_hold_edit():
    first = make_message(1, email="asha@example.com")
    second = make_message(2, name="Ravi Kumar", email="ravi@example.com")
    third = make_message(3, name="Meera Shah", email="meera@example.com")
    # Neutral category: this test is about the a/h/e keys. Reschedule offers also need a
    # time before approval, which tests/test_reschedule.py covers.
    groups = {"application_status": [first, second], "other": [third]}
    drafts = {
        1: Draft(id=1, reply="Hi Asha, I'll check with the team."),
        2: Draft(id=2, reply="Hi Ravi, thanks for writing."),
        3: Draft(id=3, reply="Hi Meera, noted."),
    }
    answers = iter(["a", "h", "e", "Hi Meera, thanks, I'll get back to you soon."])
    held = []

    approved = review(groups, drafts, {}, input_fn=lambda prompt: next(answers), held=held)

    assert approved == [
        {"id": 1, "to": "asha@example.com", "reply": "Hi Asha, I'll check with the team.", "edited": False},
        {"id": 3, "to": "meera@example.com", "reply": "Hi Meera, thanks, I'll get back to you soon.", "edited": True},
    ]
    # Day 5: the terminal said [r]eject while the UI had replaced Reject with Hold on Day 2.
    # A held message is kept and listed, never silently dropped.
    assert held == [{"id": 2, "to": "ravi@example.com", "draft": "Hi Ravi, thanks for writing."}]


def test_terminal_review_offers_hold_not_reject(capsys):
    prompts = []
    answers = iter(["r", "h"])  # "r" is no longer a choice

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    review({"other": [make_message(1)]}, {1: Draft(id=1, reply="Hi Asha.")}, {}, input_fn=answer)
    assert "[h]old" in prompts[0] and "reject" not in prompts[0].lower()
    assert "1 on hold" in capsys.readouterr().out


def test_review_reasks_on_unknown_key():
    message = make_message(1)
    answers = iter(["x", "", "a"])

    approved = review(
        {"other": [message]}, {1: Draft(id=1, reply="Hi Asha.")}, {},
        input_fn=lambda prompt: next(answers),
    )

    assert [item["id"] for item in approved] == [1]
