"""The fictional demo world (BUILD.md §17.1): Northpeak Outfitters.

All people, documents, policies and numbers are invented; every email uses example.com.
This module is the single source of truth for:
  - personas, background users, groups and knowledge collections;
  - the document contents (rendered to DOCX/Markdown/HTML/PDF by demo/generate.py);
  - the traffic phrase banks used by demo/generate.py;
  - the *oracle*: the ground truth the Demo LLM answers structured prompts from.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Any

# ============================================================================ people
PERSONAS: list[dict[str, Any]] = [
    {"id": "ava", "name": "Ava", "role": "Support agent", "groups": ["support"], "is_admin": False},
    {"id": "ben", "name": "Ben", "role": "Support lead", "groups": ["support", "managers"], "is_admin": False},
    {"id": "chen", "name": "Chen", "role": "HR partner", "groups": ["hr", "managers"], "is_admin": False},
    {"id": "dana", "name": "Dana", "role": "New hire in sales", "groups": [], "is_admin": False},
    {"id": "eli", "name": "Eli", "role": "Platform admin", "groups": ["admins"], "is_admin": True},
]
BACKGROUND_USERS: list[dict[str, Any]] = [
    {"id": f"user{i:02d}", "name": f"Northpeak user {i:02d}", "role": "Background traffic",
     "groups": (["support"] if i <= 10 else []) + (["managers"] if i in (3, 14) else []), "is_admin": False}
    for i in range(1, 21)
]
ALL_USERS = PERSONAS + BACKGROUND_USERS

# ============================================================================ knowledge
VACATION_V1: dict[str, int] = {
    "France": 25, "Germany": 30, "Spain": 23, "Italy": 26, "United Kingdom": 25,
    "Netherlands": 24, "Ireland": 21, "Sweden": 25, "Poland": 26, "United States": 15,
}
VACATION_V2: dict[str, int] = {**VACATION_V1, "Germany": 32, "Portugal": 22}

COLLECTIONS: list[dict[str, Any]] = [
    {"key": "handbook", "name": "Employee Handbook", "access": "org", "group_ids": []},
    {"key": "products", "name": "Products & Returns", "access": "org", "group_ids": []},
    {"key": "playbook", "name": "Support Playbook", "access": "group", "group_ids": ["support"]},
    {"key": "compensation", "name": "Compensation Guide", "access": "group", "group_ids": ["managers"]},
]


@dataclass
class DocSpec:
    key: str
    collection: str
    title: str
    fmt: str                     # docx | md | html | pdf
    filename: str
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    table: tuple[str, list[str], list[list[str]]] | None = None   # (heading, header, rows)


def _handbook(version: int) -> DocSpec:
    days = VACATION_V1 if version == 1 else VACATION_V2
    rows = [[c, str(d)] for c, d in days.items()]
    intro = ("This handbook describes the employment policies of Northpeak Outfitters. "
             f"Version {version}. It applies to every employee unless a local contract says otherwise.")
    return DocSpec(
        key=f"handbook_v{version}", collection="handbook", title="Employee Handbook", fmt="docx",
        filename=f"handbook_v{version}.docx",
        sections=[
            ("About this handbook", [intro]),
            ("Parental leave", [
                "Northpeak offers 16 weeks of fully paid parental leave to every new parent, including "
                "adoptive parents.",
                "Parental leave can be taken in up to three blocks within the first 18 months after the "
                "birth or adoption. Tell your manager at least 8 weeks before your leave starts.",
            ]),
            ("Expenses", [
                "Meals during business travel are reimbursed up to 45 euros per day.",
                "Submit expense claims with receipts within 30 days. Hotel stays are booked through the "
                "travel desk; economy class applies to flights under six hours.",
            ]),
            ("Remote work", [
                "Employees may work remotely up to 3 days per week, agreed with their manager.",
                "Northpeak provides a one-off home office allowance of 300 euros for a desk chair and "
                "screen.",
            ]),
            ("Learning budget", [
                "Each employee has an annual learning budget of 1,000 euros for courses, books and "
                "conferences.",
            ]),
        ],
        table=("Vacation days by country", ["Country", "Vacation days per year"], rows),
    )


DOCUMENTS: list[DocSpec] = [
    _handbook(1),
    DocSpec(key="product_faq", collection="products", title="Product FAQ", fmt="md", filename="product_faq.md",
            sections=[
                ("Ridgeline 2 tent", [
                    "The Ridgeline 2 is a two-person backpacking tent weighing 1.9 kg with a 3,000 mm "
                    "waterproof rating on the fly.",
                    "Replacement tent poles are free within the two-year warranty; order them from the "
                    "support portal.",
                ]),
                ("Summit hiking boots", [
                    "Summit boots run half a size small, so we recommend ordering half a size up.",
                    "Break the boots in for about 20 km of easy walking before a long trek.",
                ]),
                ("Warranty", [
                    "All Northpeak gear has a two-year warranty against manufacturing defects.",
                    "The warranty does not cover normal wear, misuse or damage from fire.",
                ]),
                ("Down jacket care", [
                    "Wash down jackets on a gentle cycle with down-specific soap and tumble dry on low "
                    "heat with dryer balls.",
                ]),
            ]),
    DocSpec(key="returns_policy", collection="products", title="Returns Policy", fmt="html",
            filename="returns_policy.html",
            sections=[
                ("Return window", [
                    "You can return unused items within 30 days of delivery for a full refund.",
                    "Items must be in their original packaging with tags attached.",
                ]),
                ("Refunds", [
                    "Refunds are processed within 5 business days after we receive the returned item.",
                    "The refund goes back to the original payment method.",
                ]),
                ("Return shipping", [
                    "Return shipping is free for Northpeak Club members; other customers pay a flat "
                    "return fee of 4.95 euros.",
                ]),
                ("Exchanges", [
                    "Size exchanges are free and ship as soon as the original item is scanned by the "
                    "carrier.",
                ]),
            ]),
    DocSpec(key="escalation_guide", collection="playbook", title="Escalation Guide", fmt="pdf",
            filename="escalation_guide.pdf",
            sections=[
                ("When to escalate", [
                    "Escalate a ticket to Tier 2 when a customer reports a safety issue, when a refund "
                    "exceeds 200 euros, or when the same order has three or more contacts.",
                ]),
                ("Response times", [
                    "Urgent tickets must receive a first response within 4 hours; standard tickets "
                    "within one business day.",
                ]),
                ("On-call", [
                    "Outside office hours, page the on-call support lead through the support rota. "
                    "Safety issues are escalated immediately, at any time.",
                ]),
            ]),
    DocSpec(key="compensation_guide", collection="compensation", title="Compensation Guide", fmt="pdf",
            filename="compensation_guide.pdf",
            sections=[
                ("Pay bands", [
                    "Associate pay band: 38,000 to 46,000 euros. Team lead pay band: 55,000 to 68,000 "
                    "euros. Manager pay band: 70,000 to 90,000 euros.",
                ]),
                ("Bonus policy", [
                    "The bonus range for team leads is 8 to 12 percent of base salary, paid annually in "
                    "March.",
                    "Managers have a bonus range of 10 to 15 percent of base salary.",
                ]),
            ]),
]
HANDBOOK_V2 = _handbook(2)


def doc_plain_text(d: DocSpec) -> str:
    parts: list[str] = [d.title]
    for heading, paras in d.sections:
        parts.append(heading)
        parts.extend(paras)
    if d.table:
        heading, header, rows = d.table
        parts.append(heading)
        parts.append(" | ".join(header))
        parts.extend(" | ".join(r) for r in rows)
    return "\n".join(parts)


# Knowledge questions: (question, collection key, expected answer fragment)
KNOWLEDGE_QUESTIONS: list[tuple[str, str, str]] = [
    ("How many weeks of parental leave do we offer?", "handbook", "16 weeks"),
    ("How much parental leave do new parents get?", "handbook", "16 weeks"),
    ("What is the daily meal allowance on business trips?", "handbook", "45 euros"),
    ("How many days a week can I work remotely?", "handbook", "3 days"),
    ("What is the annual learning budget?", "handbook", "1,000 euros"),
    ("How long do I have to submit expense claims?", "handbook", "30 days"),
    ("How much does the Ridgeline 2 tent weigh?", "products", "1.9 kg"),
    ("Do Summit boots run small?", "products", "half a size"),
    ("How long is the warranty on Northpeak gear?", "products", "two-year"),
    ("How long do I have to return an item?", "products", "30 days"),
    ("How quickly are refunds processed?", "products", "5 business days"),
    ("Is return shipping free?", "products", "Club members"),
    ("How do I wash a down jacket?", "products", "gentle cycle"),
    ("When should I escalate a ticket to Tier 2?", "playbook", "safety issue"),
    ("What is the first response time for urgent tickets?", "playbook", "4 hours"),
    ("What's the bonus range for team leads?", "compensation", "8 to 12 percent"),
    ("What is the pay band for a team lead?", "compensation", "55,000"),
]

VACATION_Q = [
    "How many vacation days do employees in {c} get?",
    "How many vacation days in {c}?",
    "What is the annual leave allowance in {c}?",
    "How many days of vacation do staff in {c} get per year?",
]

# Prompts the live demo asks on stage (BUILD.md §17.2). They never appear in the seeded week, so the
# scripted "first time" is really the first time (e.g. S1 step 2 goes to the LLM, S4 step 1 too).
DEMO_SCRIPT_PROMPTS = {
    "Draft a friendly reply to a customer whose tent pole broke",
    "How many weeks of parental leave do we offer?",
    "What's the bonus range for team leads?",
    "Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'",
    "Classify this support ticket: 'The tent pole snapped on the first night'",
}

# ============================================================================ traffic phrase banks
PRODUCTS = ["tent", "Ridgeline 2 tent", "sleeping bag", "down jacket", "hiking boots", "Summit boots",
            "backpack", "rain shell", "trekking poles", "headlamp", "camp stove", "fleece"]
FIRST = ["jane", "omar", "li", "sofia", "marco", "ines", "tom", "aisha", "piet", "maria", "noah", "lena"]

TICKET_BANK: dict[str, list[str]] = {
    # class: phrasings. Roughly a third avoid the obvious keywords a simple rule would use.
    "billing": [
        "I was charged twice for my {p} order",
        "my card was charged {a} but the order total was lower",
        "the invoice for order {o} shows the wrong amount",
        "you took {a} from my account two times",
        "there are two identical payments of {a} on my statement",
        "why is my bank showing a pending amount of {a} for an order I cancelled",
        "the price went up between the basket and the checkout for my {p}",
        "the discount code was not applied and I paid full price for the {p}",
        "I need a VAT receipt with my company name for order {o}",
        "I was billed {a} for a {p} I never ordered",
        "the payment for order {o} went through twice",
        "my invoice is missing the discount",
    ],
    "shipping": [
        "my {p} hasn't arrived yet and it's been {d} days",
        "the tracking for order {o} hasn't updated in a week",
        "where is my parcel? I ordered the {p} {d} days ago",
        "the courier left my {p} at the wrong address",
        "the box with my {p} came completely crushed by the carrier",
        "can you send my order {o} to my office instead of home",
        "it says delivered but nothing came, order {o}",
        "the parcel has been stuck in the depot since monday",
        "the {p} was supposed to be here by friday and still nothing",
        "when will my {p} be delivered",
        "the delivery date for order {o} keeps moving",
        "do you ship to Norway",
        "my tracking number doesn't work",
        "the {p} still hasn't arrived",
        "how much is express shipping",
    ],
    "returns": [
        "how do I return the {p}, it doesn't fit",
        "I'd like to send the {p} back, it's too small",
        "can I swap the {p} for a bigger size",
        "please send me a return label for order {o}",
        "the {p} isn't what I expected and I want to send it back",
        "is it too late to send back the {p} I bought {d} days ago",
        "I sent the {p} back and want to exchange it for the next size up",
        "the colour of the {p} is different from the photos, can I send it back for another one",
        "I want to return my {p}",
        "can I return the {p} without the original box",
        "the return label for order {o} doesn't print",
        "how many days do I have to return the {p}",
        "I'd like to return one of the two {p}s I ordered",
        "can I return a gift without a receipt",
        "where do I drop off a return",
    ],
    "refund": [
        "I returned the {p} {d} days ago and still haven't got my money back",
        "where is my refund for order {o}",
        "I sent the {p} back last week, when will I get my money",
        "I want my money back for the {p}",
        "please reimburse the {a} for the {p} I sent back",
        "the {p} went back to you {d} days ago but the money is not on my card yet",
        "credit my card for the {p} I returned, I don't want a replacement",
        "I want a refund for the {p}",
        "how long does a refund take",
        "my refund for order {o} is short by {a}",
        "can I get a refund instead of store credit",
        "the refund for my {p} never arrived",
    ],
    "technical": [
        "the {p} pole snapped on the first night",
        "the zip on my {p} broke after two uses",
        "my {p} is leaking at the seams in light rain",
        "the {p} stopped working after one charge",
        "the sole of my {p} is coming off",
        "the buckle on the {p} cracked on day one",
        "water gets in through the seams of the {p}",
        "the {p} won't light any more",
        "the stitching on the {p} is coming apart",
    ],
    "account": [
        "I can't log in to my account",
        "I forgot my password and the reset email never comes",
        "please delete my account and all my data",
        "how do I change the email address on my profile",
        "the site says my email is already registered but I never signed up",
        "I can't sign in since the update",
        "my Club membership points disappeared from my profile",
        "the two-factor code never arrives on my phone",
        "my account is locked after too many attempts",
        "I need to reset my password",
        "I can't log in with my Club login",
    ],
}
TICKET_PREFIX = ["", "", "", "Hi, ", "Hello team, ", "Hey, ", "Good morning. ", "Quick question: "]
TICKET_SUFFIX = ["", "", "", ". Thanks!", ". Please help.", ". This is really frustrating.",
                 ". Can you sort this out?", ". Thanks in advance."]
TRIAGE_CLASSES = list(TICKET_BANK)
TRIAGE_PROMPTS = [
    "Classify this support ticket: '{t}'",
    "Classify this support ticket into billing, shipping, returns, refund, technical or account: '{t}'",
    "Which category is this ticket? '{t}'",
]

# Phrase-level signatures the oracle uses (an "LLM that understands" the ticket).
_SIGNATURES: dict[str, list[str]] = {
    cls: [max(re.split(r"\{[a-z]\}", ph), key=len).strip().lower() for ph in phrases]
    for cls, phrases in TICKET_BANK.items()
}

UNITS: dict[str, tuple[str, float, str]] = {
    # name -> (dimension, factor to SI base, canonical label)
    "km": ("length", 1000.0, "km"), "kilometers": ("length", 1000.0, "km"), "kilometres": ("length", 1000.0, "km"),
    "miles": ("length", 1609.344, "miles"), "mile": ("length", 1609.344, "miles"), "mi": ("length", 1609.344, "miles"),
    "m": ("length", 1.0, "m"), "meters": ("length", 1.0, "m"), "metres": ("length", 1.0, "m"),
    "feet": ("length", 0.3048, "feet"), "ft": ("length", 0.3048, "feet"),
    "kg": ("mass", 1.0, "kg"), "kilograms": ("mass", 1.0, "kg"),
    "lb": ("mass", 0.45359237, "lb"), "lbs": ("mass", 0.45359237, "lb"), "pounds": ("mass", 0.45359237, "lb"),
    "g": ("mass", 0.001, "g"), "grams": ("mass", 0.001, "g"), "oz": ("mass", 0.028349523125, "oz"),
    "ounces": ("mass", 0.028349523125, "oz"),
    "liters": ("volume", 1.0, "liters"), "litres": ("volume", 1.0, "liters"), "l": ("volume", 1.0, "liters"),
    "gallons": ("volume", 3.785411784, "gallons"),
}
CONVERSION_PAIRS = [("km", "miles"), ("miles", "km"), ("kg", "lb"), ("lb", "kg"), ("m", "feet"),
                    ("feet", "m"), ("g", "oz"), ("liters", "gallons")]
CONVERSION_PROMPTS = ["Convert {n} {u1} to {u2}", "convert {n} {u1} into {u2}", "How many {u2} is {n} {u1}?",
                      "Convert {n} {u1} to {u2} please"]

EXTRACTION_PROMPTS = [
    "Extract the order number, email and amount: '{s}'",
    "Extract the order number, email and amount from this message: '{s}'",
]
EXTRACTION_SENTENCES = [
    "Order {o} for {e} was charged {cur}{amt} twice",
    "Customer {e} says order {o} cost {cur}{amt} but the receipt is missing",
    "Refund {cur}{amt} on order {o} to {e}",
    "{e} placed order {o} and paid {cur}{amt}",
    "Payment of {cur}{amt} for {o} failed for {e}",
]
CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}

URGENCY_PROMPTS = ["Rate the urgency 1-5: '{t}'", "Rate the urgency 1–5: '{t}'",
                   "On a scale of 1 to 5, how urgent is this: '{t}'"]
URG_DEADLINE = [("my trek starts tomorrow", 2), ("I leave for the mountains today", 2),
                ("we fly out tonight", 2), ("the race is this weekend", 1), ("my trip is next week", 1),
                ("my trip is in two months", 0), ("", 0), ("", 0)]
URG_PROBLEM = [("and my boots haven't arrived", 1), ("and the tent pole snapped", 1),
               ("and the stove is leaking gas", 2), ("and I have a question about sizing", -1),
               ("and I'd like to know the colour options", -1), ("and my jacket zip is stuck", 0),
               ("and the order is missing the headlamp", 1)]
URG_TONE = [("", 0), ("", 0), ("no rush at all", -1), ("please help ASAP", 1), ("this is a safety issue", 1),
            ("just curious", -1)]

OPEN_ENDED = [
    "Draft a friendly reply to a customer whose tent pole broke",
    "Draft a friendly reply to a customer whose {p} arrived late",
    "Write a product description for the {p}",
    "Summarize this customer review: 'The {p} was great on the trail but a bit heavy'",
    "Write a short Instagram caption for our new {p}",
    "Draft an apology email for a delayed order of {p}",
    "Brainstorm three names for a lightweight {p}",
    "Write a two-line welcome message for new Northpeak Club members",
]

ROUTING_PROMPTS = ["Which queue should handle this ticket from {e}: '{t}'"]
QUEUES = {"gold": "priority-", "silver": "", "standard": ""}

# CRM tier table served by crm-mock (S9).
CRM_TIERS: dict[str, str] = {f"{n}@example.com": ("gold" if i % 4 == 0 else "silver" if i % 4 == 1 else "standard")
                             for i, n in enumerate(FIRST)}


# ============================================================================ oracle
_QUOTED = re.compile(r"['\"‘’“”](.+)['\"‘’“”]\s*\??\s*$", re.S)


def quoted(prompt: str) -> str:
    m = _QUOTED.search(prompt.strip())
    return m.group(1).strip() if m else prompt.split(":", 1)[-1].strip()


def classify_ticket(text: str) -> str:
    """Oracle triage: phrase-signature match against the generator banks (an 'LLM that reads it')."""
    t = text.lower()
    best, best_len = "technical", 0
    for cls, sigs in _SIGNATURES.items():
        for sig in sigs:
            sig = sig.strip(" .,'")
            if len(sig) >= 6 and sig in t and len(sig) > best_len:
                best, best_len = cls, len(sig)
    if best_len:
        return best
    # fallback for free text typed live: meaning-level cues
    cues = [("refund", ["money back", "refund", "reimburse", "credit my card"]),
            ("returns", ["return", "send it back", "send back", "exchange", "swap"]),
            ("billing", ["charged", "invoice", "payment", "price", "receipt", "discount"]),
            ("shipping", ["arrive", "tracking", "parcel", "courier", "delivered", "deliver"]),
            ("account", ["log in", "login", "password", "account", "sign in", "profile", "email address"]),
            ("technical", ["broke", "snapped", "leak", "stopped working", "crack", "coming off", "apart"])]
    for cls, words in cues:
        if any(w in t for w in words):
            return cls
    return "technical"


def urgency_score(text: str) -> int:
    t = text.lower()
    score = 2
    for bank in (URG_DEADLINE, URG_PROBLEM, URG_TONE):
        for phrase, delta in bank:
            if phrase and phrase.lower() in t:
                score += delta
    return max(1, min(5, score))


_ORDER = re.compile(r"\bNP-\d{4,6}\b", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_AMOUNT = re.compile(r"([€$£])\s?(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s?(EUR|USD|GBP)", re.I)


def extract_order(text: str) -> str:
    order = _ORDER.search(text)
    email = _EMAIL.search(text)
    amt = _AMOUNT.search(text)
    amount = currency = None
    if amt:
        if amt.group(1):
            currency, amount = CURRENCIES[amt.group(1)], amt.group(2)
        else:
            currency, amount = amt.group(4).upper(), amt.group(3)
        amount = f"{float(amount.replace(',', '.')):.2f}"
    return json.dumps({"order_number": order.group(0).upper() if order else None,
                       "email": email.group(0).lower() if email else None,
                       "amount": amount, "currency": currency}, sort_keys=True)


_CONV = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s+(?:to|into|in)\s+([a-zA-Z]+)", re.I)
_CONV_Q = re.compile(r"how many\s+([a-zA-Z]+)\s+(?:is|are|in)\s+(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)", re.I)


def convert_units(prompt: str) -> str | None:
    m = _CONV.search(prompt)
    if m:
        n, u1, u2 = float(m.group(1)), m.group(2).lower(), m.group(3).lower()
    else:
        q = _CONV_Q.search(prompt)
        if not q:
            return None
        u2, n, u1 = q.group(1).lower(), float(q.group(2)), q.group(3).lower()
    if u1 not in UNITS or u2 not in UNITS or UNITS[u1][0] != UNITS[u2][0]:
        return None
    value = n * UNITS[u1][1] / UNITS[u2][1]
    return f"{_fmt_num(n)} {UNITS[u1][2]} = {value:.2f} {UNITS[u2][2]}"


def _fmt_num(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def route_queue(prompt: str) -> str:
    email = _EMAIL.search(prompt)
    tier = CRM_TIERS.get(email.group(0).lower(), "standard") if email else "standard"
    return QUEUES[tier] + classify_ticket(quoted(prompt))


def vacation_country(prompt: str) -> str | None:
    countries = sorted(set(VACATION_V2) | {"UK", "USA", "US"}, key=len, reverse=True)
    low = prompt.lower()
    for c in countries:
        if re.search(rf"\b{re.escape(c.lower())}\b", low):
            return {"UK": "United Kingdom", "USA": "United States", "US": "United States"}.get(c, c)
    return None


def detect_task(prompt: str) -> str | None:
    """Which structured task (if any) a prompt asks for. Mirrors the generator's templates."""
    p = prompt.strip().lower()
    if p.startswith(("classify this support ticket", "which category is this ticket")):
        return "triage"
    if p.startswith("extract the order number"):
        return "extraction"
    if p.startswith(("rate the urgency", "on a scale of 1 to 5")):
        return "urgency"
    if p.startswith("which queue should handle"):
        return "routing"
    if (p.startswith("convert ") or p.startswith("how many ")) and convert_units(prompt):
        return "conversion"
    if ("vacation" in p or "annual leave" in p) and vacation_country(prompt):
        return "vacation"
    return None


def oracle(prompt: str) -> str | None:
    """Ground-truth answer for a structured prompt, or None if the prompt is not structured."""
    task = detect_task(prompt)
    if task == "triage":
        return classify_ticket(quoted(prompt))
    if task == "extraction":
        return extract_order(quoted(prompt))
    if task == "urgency":
        return str(urgency_score(quoted(prompt)))
    if task == "routing":
        return route_queue(prompt)
    if task == "conversion":
        return convert_units(prompt)
    from .demo_scenarios import oracle as scenario_oracle  # the ml / dl scenarios (review sentiment, ...)

    return scenario_oracle(prompt)


def vacation_answer(country: str, days: int) -> str:
    return f"Employees in {country} get {days} vacation days per year."


# ============================================================================ generators
def make_ticket(rng: random.Random, cls: str) -> str:
    phrase = rng.choice(TICKET_BANK[cls])
    body = phrase.format(p=rng.choice(PRODUCTS), a=f"€{rng.randint(20, 400)}.{rng.choice(['00', '90', '50', '99'])}",
                         o=f"NP-{rng.randint(10000, 99999)}", d=rng.randint(3, 21))
    text = rng.choice(TICKET_PREFIX) + body + rng.choice(TICKET_SUFFIX)
    return text[0].upper() + text[1:]


def make_triage_prompt(rng: random.Random, cls: str) -> tuple[str, str]:
    t = make_ticket(rng, cls)
    return rng.choice(TRIAGE_PROMPTS).format(t=t), t


def make_extraction(rng: random.Random) -> str:
    sym = rng.choice(list(CURRENCIES))
    s = rng.choice(EXTRACTION_SENTENCES).format(
        o=f"NP-{rng.randint(10000, 99999)}", e=f"{rng.choice(FIRST)}@example.com", cur=sym,
        amt=f"{rng.randint(5, 600)}.{rng.choice(['00', '90', '50', '99', '25'])}")
    return rng.choice(EXTRACTION_PROMPTS).format(s=s)


def make_conversion(rng: random.Random) -> str:
    u1, u2 = rng.choice(CONVERSION_PAIRS)
    n = rng.choice([1, 2, 3, 5, 8, 10, 12, 15, 20, 21, 25, 42, 50, 100])
    return rng.choice(CONVERSION_PROMPTS).format(n=n, u1=u1, u2=u2)


def make_urgency_text(rng: random.Random) -> str:
    d = rng.choice(URG_DEADLINE)[0]
    pr = rng.choice(URG_PROBLEM)[0]
    tone = rng.choice(URG_TONE)[0]
    lead = d if d else rng.choice(["I placed an order for a {p}", "I bought a {p}", "I'm writing about my {p} order",
                                   "My {p} order"]).format(p=rng.choice(PRODUCTS))
    text = f"{lead} {pr}".strip()
    if tone:
        text += f", {tone}"
    return text[0].upper() + text[1:]


def make_urgency(rng: random.Random) -> str:
    return rng.choice(URGENCY_PROMPTS).format(t=make_urgency_text(rng))


def make_open_ended(rng: random.Random) -> str:
    return rng.choice(OPEN_ENDED).format(p=rng.choice(PRODUCTS))


def make_routing(rng: random.Random) -> str:
    cls = rng.choice(TRIAGE_CLASSES)
    return ROUTING_PROMPTS[0].format(e=f"{rng.choice(FIRST)}@example.com", t=make_ticket(rng, cls))


def keyword_rule(text: str) -> str:
    """The naive keyword rule an agent writes first (iteration 1). Used by the generator to
    check that keyword rules reach only ~60-75 % agreement on the triage traffic."""
    t = text.lower()
    if "refund" in t:
        return "refund"
    if "return" in t:
        return "returns"
    if any(w in t for w in ("charged", "invoice", "payment", "bill")):
        return "billing"
    if any(w in t for w in ("ship", "deliver", "tracking", "arriv")):
        return "shipping"
    if any(w in t for w in ("password", "log in", "login", "account", "sign in")):
        return "account"
    return "technical"


# ============================================================================ open-ended text (Demo LLM)
def open_ended_answer(prompt: str) -> str:
    p = prompt.lower()
    product = next((x for x in sorted(PRODUCTS, key=len, reverse=True) if x.lower() in p), "gear")
    if "reply" in p or "apology" in p or "email" in p:
        return (f"Hi there,\n\nThank you for getting in touch, and I'm sorry to hear about your {product}. "
                "That's not the experience we want for you on the trail. We've arranged a free replacement, "
                "and it will ship today with express delivery. If anything else comes up, just reply to this "
                "message and we'll sort it out straight away.\n\nWarm regards,\nThe Northpeak Support Team")
    if "description" in p:
        return (f"The Northpeak {product} is built for long days outside: light enough to forget it's there, "
                "tough enough for rough weather, and backed by our two-year warranty.")
    if "summarize" in p or "summary" in p:
        return f"The customer liked the {product} on the trail but found it heavier than expected."
    if "caption" in p:
        return f"New season, new trails. Meet the {product} — light, tough and ready when you are. #Northpeak"
    if "names" in p or "brainstorm" in p:
        return "1. Featherline\n2. Ridge Whisper\n3. Summit Air"
    if "welcome" in p:
        return ("Welcome to the Northpeak Club! Free return shipping, early access to new gear and member "
                "prices start today.")
    return ("Here's a draft to get you started. Northpeak Outfitters makes dependable outdoor gear for people "
            "who'd rather be outside, and every item is backed by a two-year warranty and friendly support.")
