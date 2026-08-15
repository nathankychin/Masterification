import pytest
from app import evaluate_response, generate_creative_phrase


def test_evaluate_igcse_keywords_and_reasoning():
    resp = "First, do this. Then, because of X, do Y. join select where group from"
    score = evaluate_response("SQL", resp, exam_board="IGCSE")
    assert score >= 40


def test_evaluate_alevel_higher_requirement():
    resp = "This is a short answer without keywords."
    score = evaluate_response("Chemistry", resp, exam_board="A-LEVEL")
    assert score <= 40


def test_generate_phrase_nonempty():
    phrase = generate_creative_phrase('general')
    assert isinstance(phrase, str) and len(phrase) > 5
