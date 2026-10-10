"""Stage 1 of the light layer (BUILD.md §4.1): normalize & fingerprint.

`normalize(prompt, conversation)` returns the normalized text, a template with typed
placeholders, the extracted slots, the cache keys and the volatile / context-dependent flags.
Pure functions, no I/O.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})

# Order matters: earlier patterns claim their span first.
_SLOT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Quotes must not touch letters on the outside, so contractions ("what's", "haven't")
    # neither open nor close a quoted payload; apostrophes inside the payload are fine.
    ("TEXT", re.compile(r"(?<!\w)'(?=\S).+?(?<=\S)'(?!\w)|(?<!\w)\"(?=\S).+?(?<=\S)\"(?!\w)")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("URL", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    ("ID", re.compile(r"\b[A-Z]{2,5}-\d{3,}\b|\b\d{6,}\b")),
    ("DATE", re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|"
                        r"jul|aug|sep|oct|nov|dec)[a-z]*\b(?:\s+\d{4})?", re.I)),
    ("AMOUNT", re.compile(r"[€$£]\s?\d+(?:[.,]\d+)?")),
    ("CURRENCY", re.compile(r"\b(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|SEK|NOK|DKK|PLN)\b", re.I)),
    # Units change the answer ("5 km to miles" vs "5 miles to km"), so they are slots too.
    # Single-letter units count only right after a number, so "I'm" or "a g-string" never match.
    ("UNIT", re.compile(r"\b(?:km|kilomet(?:er|re)s?|miles?|mi|met(?:er|re)s?|feet|foot|ft|cm|centimet(?:er|re)s?|"
                        r"inch(?:es)?|kg|kilograms?|lbs?|pounds?|grams?|oz|ounces?|lit(?:er|re)s?|gallons?|"
                        r"celsius|fahrenheit|mph|kph)\b|(?<=\d)\s?(?:m|g|l)\b", re.I)),
    ("NUM", re.compile(r"(?<![\w<])-?\d+(?:[.,]\d+)?(?=$|[^\w>]|(?:km|kg|lbs?|mi|ft|oz|cm|mph|kph|m|g|l)\b)", re.I)),
]
_UNIT_CANON = {"kilometers": "km", "kilometres": "km", "kilometer": "km", "kilometre": "km", "mile": "miles",
               "mi": "miles", "meters": "m", "metres": "m", "meter": "m", "metre": "m", "foot": "feet", "ft": "feet",
               "kilograms": "kg", "kilogram": "kg", "lbs": "lb", "pounds": "lb", "pound": "lb", "grams": "g",
               "gram": "g", "ounces": "oz", "ounce": "oz", "liters": "l", "litres": "l", "liter": "l",
               "litre": "l", "gallon": "gallons", "inch": "inches", "centimeters": "cm", "centimetres": "cm"}

# Proper nouns (capitalised words that don't start a sentence): countries, people, places.
# They change the answer ("France" vs "Spain"), so they are slots, never part of the template.
_NAME = re.compile(r"(?<=[a-z,;:]\s)(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)*")
_NOT_NAMES = {"I", "I'm", "I'd", "Ok", "Please", "Thanks", "Hi", "Hello", "Club"}

_VOLATILE = re.compile(r"\b(today|today's|tonight|now|right now|latest|current|currently|at the moment|live|"
                       r"real[- ]time|this morning|so far)\b", re.I)
_FOLLOWUP_START = re.compile(r"^(?:and\s+)?(?:what about|how about|and for|and what|what if|same for|same with|"
                             r"and in|and the|and also|also for|then for)\b|^and\s", re.I)
_PRONOUN_START = re.compile(r"^(?:it|that|those|these|they|them|this one|that one|he|she)\b", re.I)


@dataclass(frozen=True)
class Normalized:
    text: str                                  # normalized prompt (with slot values)
    template: str                              # e.g. "convert <NUM> km to miles"
    slots: dict[str, list[str]] = field(default_factory=dict)
    exact_key: str = ""                        # sha256(normalized)  (scope/fingerprint added by the cache)
    template_key: str = ""                     # sha256(template)
    volatile: bool = False
    context_dependent: bool = False

    def scoped_key(self, scope: str, fingerprint: str | None = None) -> str:
        """exact_key = sha256(scope + normalized [+ context fingerprint]) (§4.1, §4.5)."""
        return sha256(f"{scope}|{fingerprint or ''}|{self.text}")


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def clean(prompt: str) -> str:
    s = unicodedata.normalize("NFKC", prompt).translate(_QUOTES)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"[\s.!?]+$", "", s)


def extract_slots(cleaned: str) -> tuple[str, dict[str, list[str]]]:
    """Replace variable parts with typed placeholders. Returns (template, slots).

    Slot values are compared case-insensitively; their order of appearance is kept.
    """
    spans: list[tuple[int, int, str, str]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e, _, _ in spans)

    for kind, rx in _SLOT_PATTERNS:
        for m in rx.finditer(cleaned):
            if free(m.start(), m.end()):
                val = m.group(0)
                if kind == "TEXT":
                    val = val[1:-1]
                if kind == "UNIT":
                    val = _UNIT_CANON.get(val.strip().lower(), val.strip().lower())
                spans.append((m.start(), m.end(), kind, val.strip().lower()))
    for m in _NAME.finditer(cleaned):
        if m.group(0) not in _NOT_NAMES and free(m.start(), m.end()):
            spans.append((m.start(), m.end(), "NAME", m.group(0).lower()))
    spans.sort()
    out: list[str] = []
    slots: dict[str, list[str]] = {}
    pos = 0
    for s, e, kind, val in spans:
        out.append(cleaned[pos:s].lower())
        out.append(f"<{kind}>")
        slots.setdefault(kind, []).append(val)
        pos = e
    out.append(cleaned[pos:].lower())
    template = re.sub(r"\s+", " ", "".join(out)).strip()
    return template, slots


def is_context_dependent(cleaned: str, has_history: bool, short_words: int = 3) -> bool:
    if not has_history:
        return False
    if _FOLLOWUP_START.search(cleaned) or _PRONOUN_START.search(cleaned):
        return True
    return len(cleaned.split()) <= short_words


def normalize(prompt: str, has_history: bool = False, short_words: int = 3) -> Normalized:
    cleaned = clean(prompt)
    template, slots = extract_slots(cleaned)
    text = cleaned.lower()
    return Normalized(
        text=text,
        template=template,
        slots=slots,
        exact_key=sha256(text),
        template_key=sha256(template),
        # Time words inside quoted payloads (<TEXT>) don't make the *question* time-sensitive.
        volatile=bool(_VOLATILE.search(template)),
        context_dependent=is_context_dependent(cleaned, has_history, short_words),
    )
