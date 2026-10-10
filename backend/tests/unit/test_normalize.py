"""Stage 1: normalizer — slot extraction table, keys, volatile and follow-up detection."""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.light.normalize import clean, normalize


@pytest.mark.parametrize("prompt,template,slots", [
    ("Convert 12 km to miles", "convert <NUM> <UNIT> to <UNIT>", {"NUM": ["12"], "UNIT": ["km", "miles"]}),
    ("convert 5 miles to km", "convert <NUM> <UNIT> to <UNIT>", {"NUM": ["5"], "UNIT": ["miles", "km"]}),
    ("How many feet is 10m?", "how many <UNIT> is <NUM><UNIT>", {"UNIT": ["feet", "m"], "NUM": ["10"]}),
    ("I'm 5 kilometres away", "i'm <NUM> <UNIT> away", {"NUM": ["5"], "UNIT": ["km"]}),
    ("convert 5 USD to EUR", "convert <NUM> <CURRENCY> to <CURRENCY>", {"NUM": ["5"], "CURRENCY": ["usd", "eur"]}),
    ("Refund €84.90 to jane@example.com", "refund <AMOUNT> to <EMAIL>", {"AMOUNT": ["€84.90"], "EMAIL": ["jane@example.com"]}),
    ("Where is order NP-48213?", "where is order <ID>", {"ID": ["np-48213"]}),
    ("Ship it on 2026-10-20", "ship it on <DATE>", {"DATE": ["2026-10-20"]}),
    ("Deliver by 20 Oct 2026 please", "deliver by <DATE> please", {"DATE": ["20 oct 2026"]}),
    ("See https://example.com/a?b=1 now", "see <URL> now", {"URL": ["https://example.com/a?b=1"]}),
    ("What is the sentiment of: 'I love it'", "what is the sentiment of: <TEXT>", {"TEXT": ["i love it"]}),
    ("Classify this: \"tent pole snapped\"", "classify this: <TEXT>", {"TEXT": ["tent pole snapped"]}),
    ("How many vacation days in France?", "how many vacation days in <NAME>", {"NAME": ["france"]}),
    ("How many vacation days in the United Kingdom", "how many vacation days in the <NAME>", {"NAME": ["united kingdom"]}),
    ("Rate the urgency 1–5: 'boots late'", "rate the urgency <NUM>-<NUM>: <TEXT>", {"NUM": ["1", "5"], "TEXT": ["boots late"]}),
    ("Call 1234567 now", "call <ID> now", {"ID": ["1234567"]}),
])
def test_slot_table(prompt, template, slots):
    n = normalize(prompt)
    assert n.template == template
    assert n.slots == slots


def test_normalized_repeat_has_same_exact_key():
    a = normalize("Draft a friendly reply to a customer whose tent pole broke")
    b = normalize("  draft a FRIENDLY reply to a customer whose tent pole broke!! ")
    assert a.exact_key == b.exact_key and a.template_key == b.template_key
    assert a.scoped_key("user:ava") != a.scoped_key("user:ben") != a.scoped_key("global")
    assert a.scoped_key("global", "fp1") != a.scoped_key("global", "fp2")


@pytest.mark.parametrize("prompt,volatile", [
    ("What's the status of today's shipments?", True),
    ("What is the latest price of the Ridgeline 2?", True),
    ("What is the current exchange rate", True),
    ("Show me orders right now", True),
    ("Convert 12 km to miles", False),
    ("Rate the urgency 1-5: 'I leave for the mountains today'", False),   # time word inside the payload
    ("I'd like to know the colour options", False),                       # "know" is not "now"
])
def test_volatile(prompt, volatile):
    assert normalize(prompt).volatile is volatile


@pytest.mark.parametrize("prompt,has_history,dependent", [
    ("what about Germany?", True, True),
    ("and for Spain?", True, True),
    ("How about hotels?", True, True),
    ("same for 20?", True, True),
    ("it was the second one", True, True),
    ("those ones", True, True),
    ("Spain?", True, True),                                   # very short in an ongoing conversation
    ("what about Germany?", False, False),                    # nothing to follow up on
    ("How many weeks of parental leave do we offer?", True, False),
    ("Is return shipping free?", True, False),
    ("Convert 12 km to miles", True, False),
])
def test_followup_detection(prompt, has_history, dependent):
    assert normalize(prompt, has_history=has_history).context_dependent is dependent


@settings(max_examples=150, deadline=None)
@given(st.text(min_size=0, max_size=120))
def test_normalize_total_and_idempotent(text):
    n = normalize(text)
    assert n.text == clean(text).lower()
    assert normalize(n.text).text == n.text
    assert len(n.exact_key) == len(n.template_key) == 64
    # every slot value was taken from the prompt (units are stored in canonical form)
    for kind, values in n.slots.items():
        for v in values:
            assert kind == "UNIT" or v in n.text


@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=0, max_value=10**5), st.integers(min_value=0, max_value=10**5))
def test_numbers_never_share_slots_unless_equal(a, b):
    na, nb = normalize(f"Convert {a} km to miles"), normalize(f"Convert {b} km to miles")
    # (6+ digit numbers are typed as IDs, so compare template and slots together)
    assert ((na.template, na.slots) == (nb.template, nb.slots)) == (a == b)
