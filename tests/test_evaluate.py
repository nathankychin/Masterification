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


def test_igcse_weak_answer_is_penalized_without_structure():
    weak = "This is a short answer because it matters."
    strong = "First, identify the steps. Then, because the equation depends on the ratio, explain the method clearly."
    assert evaluate_response("Chemistry", weak, exam_board="IGCSE") <= 30
    assert evaluate_response("Chemistry", strong, exam_board="IGCSE") >= 50


def test_generate_phrase_nonempty():
    phrase = generate_creative_phrase('general')
    assert isinstance(phrase, str) and len(phrase) > 5
