from inbox.schema import ClassificationResponse, check_ids


def test_schema_valid_classification_parses():
    raw = '{"items": [{"id": 1, "intent": "salary_question", "confidence": 0.82}]}'

    response = ClassificationResponse.model_validate_json(raw)

    assert len(response.items) == 1
    assert response.items[0].id == 1
    assert response.items[0].intent == "salary_question"
    assert response.items[0].confidence == 0.82


def test_check_ids_reports_missing_extra_and_repeated():
    errors = check_ids({1, 2, 3}, [1, 1, 4])

    assert any("Missing" in e and "2" in e and "3" in e for e in errors)
    assert any("Extra" in e and "4" in e for e in errors)
    assert any("Repeated" in e and "1" in e for e in errors)
    assert check_ids({1, 2}, [2, 1]) == []
