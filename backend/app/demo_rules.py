"""What the Demo LLM "writes" when asked to compile a pattern or name a cluster (CASCADE_LLM=demo).

With Bedrock configured, the real model writes the rule from the same prompt. These sources are
plain, dependency-free Python defining `handle(prompt) -> str`, returning "__FALLBACK__" when the
input doesn't fit; they never contain literal user data.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from . import demo_world as world

EXTRACTION_RULE = r'''
import json
import re

_ORDER = re.compile(r"\bNP-\d{4,6}\b", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_AMOUNT = re.compile(r"([€$£])\s?(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s?(EUR|USD|GBP)", re.I)
_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}


def handle(prompt: str) -> str:
    """Extract order number, email and amount as canonical JSON."""
    if not prompt.lower().startswith("extract the order number"):
        return "__FALLBACK__"
    m = re.search(r"['\"](.+)['\"]\s*$", prompt.strip(), re.S)
    text = m.group(1) if m else prompt.split(":", 1)[-1]
    order = _ORDER.search(text)
    email = _EMAIL.search(text)
    amt = _AMOUNT.search(text)
    amount = currency = None
    if amt:
        if amt.group(1):
            currency, amount = _SYMBOLS[amt.group(1)], amt.group(2)
        else:
            currency, amount = amt.group(4).upper(), amt.group(3)
        amount = "%.2f" % float(amount.replace(",", "."))
    if not (order or email or amount):
        return "__FALLBACK__"
    return json.dumps({"order_number": order.group(0).upper() if order else None,
                       "email": email.group(0).lower() if email else None,
                       "amount": amount, "currency": currency}, sort_keys=True)
'''

CONVERSION_RULE = r'''
import re

UNITS = {
    "km": ("length", 1000.0, "km"), "kilometers": ("length", 1000.0, "km"), "kilometres": ("length", 1000.0, "km"),
    "miles": ("length", 1609.344, "miles"), "mile": ("length", 1609.344, "miles"), "mi": ("length", 1609.344, "miles"),
    "m": ("length", 1.0, "m"), "meters": ("length", 1.0, "m"), "metres": ("length", 1.0, "m"),
    "feet": ("length", 0.3048, "feet"), "ft": ("length", 0.3048, "feet"),
    "kg": ("mass", 1.0, "kg"), "kilograms": ("mass", 1.0, "kg"),
    "lb": ("mass", 0.45359237, "lb"), "lbs": ("mass", 0.45359237, "lb"), "pounds": ("mass", 0.45359237, "lb"),
    "g": ("mass", 0.001, "g"), "grams": ("mass", 0.001, "g"),
    "oz": ("mass", 0.028349523125, "oz"), "ounces": ("mass", 0.028349523125, "oz"),
    "liters": ("volume", 1.0, "liters"), "litres": ("volume", 1.0, "liters"), "l": ("volume", 1.0, "liters"),
    "gallons": ("volume", 3.785411784, "gallons"),
}
_CONV = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s+(?:to|into|in)\s+([a-zA-Z]+)", re.I)
_CONV_Q = re.compile(r"how many\s+([a-zA-Z]+)\s+(?:is|are|in)\s+(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)", re.I)


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else "%g" % n


def handle(prompt: str) -> str:
    """Convert between units of length, mass and volume."""
    m = _CONV.search(prompt)
    if m:
        n, u1, u2 = float(m.group(1)), m.group(2).lower(), m.group(3).lower()
    else:
        q = _CONV_Q.search(prompt)
        if not q:
            return "__FALLBACK__"
        u2, n, u1 = q.group(1).lower(), float(q.group(2)), q.group(3).lower()
    if u1 not in UNITS or u2 not in UNITS or UNITS[u1][0] != UNITS[u2][0]:
        return "__FALLBACK__"
    value = n * UNITS[u1][1] / UNITS[u2][1]
    return "%s %s = %.2f %s" % (_fmt(n), UNITS[u1][2], value, UNITS[u2][2])
'''

TRIAGE_KEYWORD_RULE = r'''
import re


def handle(prompt: str) -> str:
    """Keyword triage: billing, shipping, returns, refund, technical, account."""
    m = re.search(r"['\"](.+)['\"]\s*\??\s*$", prompt.strip(), re.S)
    if not m:
        return "__FALLBACK__"
    t = m.group(1).lower()
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
'''


def lookup_rule(table: dict[str, Any], answer_template: str = "Employees in {key} get {value} vacation days per year.",
                slot_aliases: dict[str, str] | None = None) -> str:
    """A `lookup` artifact: slot value -> fact from a document table (kb_dependencies recorded separately)."""
    aliases = {"UK": "United Kingdom", "USA": "United States", "US": "United States", **(slot_aliases or {})}
    return (
        "import re\n\n"
        f"TABLE = {json.dumps(table, sort_keys=True)}\n"
        f"ALIASES = {json.dumps(aliases, sort_keys=True)}\n"
        f"ANSWER = {answer_template!r}\n\n\n"
        "def handle(prompt: str) -> str:\n"
        '    """Look up a fact by slot value from the declared document table."""\n'
        "    low = prompt.lower()\n"
        "    if 'vacation' not in low and 'annual leave' not in low:\n"
        "        return '__FALLBACK__'\n"
        "    keys = sorted(list(TABLE) + list(ALIASES), key=len, reverse=True)\n"
        "    for k in keys:\n"
        "        if re.search(r'\\b' + re.escape(k.lower()) + r'\\b', low):\n"
        "            key = ALIASES.get(k, k)\n"
        "            if key in TABLE:\n"
        "                return ANSWER.format(key=key, value=TABLE[key])\n"
        "            return '__FALLBACK__'\n"
        "    return '__FALLBACK__'\n"
    )


RULES = {"extraction": EXTRACTION_RULE, "conversion": CONVERSION_RULE, "triage": TRIAGE_KEYWORD_RULE}
TASK_BY_TYPE = {"extraction": "extraction", "transform": "conversion", "classification": "triage"}


def task_of(examples: list[str]) -> str | None:
    tasks = Counter(t for t in (world.detect_task(e) for e in examples) if t)
    return tasks.most_common(1)[0][0] if tasks else None


def compile_rule(meta: dict[str, Any]) -> str:
    if meta.get("table"):
        return lookup_rule(meta["table"])
    task = meta.get("task") or task_of(list(meta.get("examples", []))) or TASK_BY_TYPE.get(str(meta.get("task_type")))
    code = RULES.get(str(task))
    if code is None:
        # Nothing the model can express as a rule: a rule that always defers to the LLM.
        return 'def handle(prompt: str) -> str:\n    return "__FALLBACK__"\n'
    return code.strip() + "\n"


CLUSTER_NAMES = {
    "triage": ("Ticket triage", "classification"),
    "extraction": ("Order details extraction", "extraction"),
    "urgency": ("Urgency scoring", "scoring"),
    "conversion": ("Unit conversion", "transform"),
    "routing": ("Priority routing", "routing"),
    "vacation": ("Vacation days by country", "lookup"),
}


def name_cluster(examples: list[str]) -> str:
    """Light-LLM cluster naming + task-type detection (discovery). Returns JSON."""
    task = task_of(examples)
    if task in CLUSTER_NAMES:
        name, ttype = CLUSTER_NAMES[task]
        return json.dumps({"name": name, "task_type": ttype, "coherent": True})
    gen = sum(1 for e in examples if e.strip().lower().startswith(("draft", "write", "summarize", "brainstorm")))
    if gen >= len(examples) / 2:
        return json.dumps({"name": "Open-ended writing", "task_type": "generation", "coherent": True})
    return json.dumps({"name": "Knowledge questions", "task_type": "qa", "coherent": True})
