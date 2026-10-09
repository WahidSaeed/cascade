"""The demo world's oracle is the Demo LLM's ground truth; it must be deterministic and correct."""
import json
import random

import pytest

from app import demo_world as w


@pytest.mark.parametrize("prompt,expected", [
    ("Convert 12 km to miles", "12 km = 7.46 miles"),
    ("convert 5 kg into lb", "5 kg = 11.02 lb"),
    ("How many feet is 10 m?", "10 m = 32.81 feet"),
    ("Convert 5 km to kg", None),
])
def test_conversion(prompt, expected):
    assert w.convert_units(prompt) == expected


def test_extraction():
    out = json.loads(w.extract_order("Order NP-48213 for jane@example.com was charged €84.90 twice"))
    assert out == {"order_number": "NP-48213", "email": "jane@example.com", "amount": "84.90", "currency": "EUR"}
    out = json.loads(w.extract_order("paid 12 USD"))
    assert out["amount"] == "12.00" and out["currency"] == "USD" and out["order_number"] is None


def test_urgency_bounds_and_cues():
    assert w.urgency_score("My trek starts tomorrow and my boots haven't arrived") == 5
    assert w.urgency_score("I placed an order and I'd like to know the colour options, just curious") == 1
    rng = random.Random(1)
    for _ in range(200):
        assert 1 <= w.urgency_score(w.make_urgency_text(rng)) <= 5


def test_ticket_oracle_matches_construction():
    rng = random.Random(7)
    for cls in w.TRIAGE_CLASSES:
        for _ in range(60):
            assert w.classify_ticket(w.make_ticket(rng, cls)) == cls


@pytest.mark.parametrize("text,cls", [
    ("I'd love my money back please", "refund"),
    ("can I exchange these", "returns"),
    ("the courier never came", "shipping"),
    ("my profile is wrong", "account"),
    ("the zipper fell apart", "technical"),
    ("hmm", "technical"),
])
def test_ticket_oracle_free_text(text, cls):
    assert w.classify_ticket(text) == cls


def test_detect_task_and_oracle():
    assert w.detect_task("Classify this support ticket: 'The tent pole snapped on the first night'") == "triage"
    assert w.oracle("Classify this support ticket: 'The tent pole snapped on the first night'") == "technical"
    assert w.oracle("Rate the urgency 1–5: 'My trek starts tomorrow and my boots haven't arrived'") == "5"
    assert w.oracle("Convert 12 km to miles") == "12 km = 7.46 miles"
    assert w.detect_task("How many vacation days do employees in France get?") == "vacation"
    assert w.oracle("How many vacation days do employees in France get?") is None
    assert w.oracle("Draft a friendly reply to a customer whose tent pole broke") is None
    q = w.oracle("Which queue should handle this ticket from jane@example.com: 'I was charged twice for my tent order'")
    assert q == "priority-billing"   # jane is a gold customer in the CRM mock
    assert w.oracle("Extract the order number, email and amount: 'Order NP-1234 for a@example.com cost $5'")


def test_vacation_country_aliases():
    assert w.vacation_country("vacation days in the UK?") == "United Kingdom"
    assert w.vacation_country("annual leave in Portugal") == "Portugal"
    assert w.vacation_country("vacation on mars") is None


def test_open_ended_answers_are_fluent():
    for p in w.OPEN_ENDED:
        text = w.open_ended_answer(p.format(p="tent"))
        assert len(text.split()) >= 5
    assert "Northpeak" in w.open_ended_answer("something else entirely")


def test_keyword_rule_is_naive():
    assert w.keyword_rule("where is my refund") == "refund"
    assert w.keyword_rule("I want my money back") == "technical"


def test_documents_and_handbook_versions():
    assert w.VACATION_V2["Germany"] == 32 and "Portugal" not in w.VACATION_V1
    text = w.doc_plain_text(w.HANDBOOK_V2)
    assert "Germany | 32" in text and "Portugal | 22" in text
    assert all(u["id"] for u in w.ALL_USERS) and len(w.ALL_USERS) == 25
