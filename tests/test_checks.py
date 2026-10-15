from inbox.checks import run_checks


def test_salary_number_is_flagged():
    flags = run_checks("Your CTC of 18 LPA is confirmed", [])

    assert "salary" in flags


def test_other_candidate_name_is_flagged():
    flags = run_checks("Rahul got an offer", ["Rahul", "Priya"])

    assert flags == ["other_candidate"]


def test_polite_reply_has_no_flags():
    reply = (
        "Hi Asha, thanks for reaching out. I've passed your request to the "
        "hiring team and will get back to you as soon as I hear from them."
    )

    assert run_checks(reply, ["Rahul", "Priya"]) == []
