"""Deterministic heuristic extractors.

These convert page text/tables into candidate structured facts. Every candidate
keeps the raw text it came from. All LLM enhancement happens *after* these
heuristics and only to disambiguate; missing info stays missing (never guessed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from backend.app.services.verifier.normalize import (
    normalize_currency,
    normalize_material,
    normalize_unit,
    normalize_text,
    parse_date,
    parse_number,
    normalize_certification,
    UNIT_ALIASES,
    delivery_weeks_to_days,
)


@dataclass
class Match:
    value: object = None
    raw_text: str = ""
    confidence: float = 0.9
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

_MATERIAL_RE = re.compile(
    r"\b(?:"
    r"(?:SS|SUS|AISI)\s?\d{3}L?"
    r"|stainless\s?steel\s?\d{3}L?"
    r"|ms\b|cs\b|mild\s?steel\b|carbon\s?steel\b"
    r"|AL\s?6061|aluminum\s?6061|aluminium\s?6061"
    r"|CU-OFHC|copper\b|brass\b|bronze\b"
    r"|PVC\b|HDPE\b|GI\b|galvanis[ez]ed\s?iron"
    r"|1\.4404|1\.4301|1\.4541"
    r"|C\s?45|EN\s?8|EN\s?9|EN\s?24"
    r")\b",
    re.IGNORECASE,
)


def extract_material(text: str) -> Optional[Match]:
    m = _MATERIAL_RE.search(text)
    if not m:
        return None
    value = m.group(0)
    norm = normalize_material(value)
    if not norm:
        return None
    confidence = 0.97 if re.search(r"\d{3}", value) else 0.9
    return Match(value=value, raw_text=text[max(0, m.start() - 40):m.end() + 40].strip(),
                 confidence=confidence)


# ---------------------------------------------------------------------------
# Quantities
# ---------------------------------------------------------------------------

_QTY_KEYS = [
    "quantity", "qty", "qty.", "required quantity", "no. of", "nos", "number",
    "no of", "total qty", "indent qty",
]
_QTY_MODIFIERS = ["minimum", "at least", "not less than", "min."]

# Build a unit pattern from aliases (longest first)
_UNIT_TOKENS = sorted(
    {t for tokens in UNIT_ALIASES.values() for t in tokens},
    key=len, reverse=True,
)
_UNIT_RE_SRC = "|".join(re.escape(t) for t in _UNIT_TOKENS)

_QTY_RE = re.compile(
    rf"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>(?:{_UNIT_RE_SRC}))(?:\b|s\b)",
    re.IGNORECASE,
)


def extract_quantity(text: str) -> Optional[Match]:
    best: Optional[tuple[float, float, str, str]] = None
    for m in _QTY_RE.finditer(text):
        number = parse_number(m.group("num"))
        if number is None:
            continue
        unit = m.group("unit")
        # Reject material-grade false positives such as 'SS 316L' -> 316 litre.
        # A unit token abutting the number ('316L', '304L') is a grade suffix,
        # not a quantity; litres are never written attached to the digit.
        if normalize_unit(unit) == "litre":
            unit_start = m.end() - len(unit)
            if text[unit_start - 1:unit_start].isdigit():
                continue
        preceding = text[:m.start()].lower()
        window = preceding[-80:]
        score = 0.5
        if any(k in window for k in _QTY_KEYS):
            score += 1.0
        if any(k in preceding for k in _QTY_MODIFIERS):
            score += 0.5
        if normalize_unit(unit) not in ("litre", "barrels"):
            score += 0.3
        if best is None or score > best[0]:
            raw = text[max(0, m.start() - 40):m.end() + 40].strip()
            best = (score, number, unit, raw)
    if best is None:
        return None
    score, number, unit, raw = best
    confidence = 0.95 if score >= 1.5 else 0.85
    return Match(value=number, raw_text=raw, confidence=confidence, extra={"unit": unit})


# ---------------------------------------------------------------------------
# Prices / budgets
# ---------------------------------------------------------------------------

_PRICE_NOUNS = [
    "budget", "maximum budget", "max budget", "estimated value", "anticipated value",
    "not exceeding", "uppermost", "ceiling", "cost limit", "price limit", "value for the supply",
]

_PRICE_RE = re.compile(
    r"(?:₹|Rs\.?|INR|USD|EUR|€|\$|GBP|£)?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<mult>lakh|lac|crore|cr|k(?!g)|thousand)?"
    r"\s*(?:INR|Rs\.?)?",
    re.IGNORECASE,
)

_PRICE_LINE_RE = re.compile(
    r"(budget|estimated\s+value|anticipated\s+value|not\s+exceeding|price\s+limit|value\s+for)"
    r".{0,60}?(?:₹|Rs\.?|INR|USD|EUR|€|\$|GBP|£)?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<mult>lakh|lac|crore|cr|k(?!g)|thousand)?",
    re.IGNORECASE,
)


def extract_budget(text: str) -> Optional[Match]:
    m = _PRICE_LINE_RE.search(text)
    if not m:
        return None
    number = parse_number(f"{m.group('num')} {m.group('mult') or ''}".strip())
    if number is None:
        return None
    currency = "INR"
    seg = text[max(0, m.start() - 30):m.end()].lower()
    for sym, cur in [("usd", "USD"), ("$", "USD"), ("eur", "EUR"), ("€", "EUR"), ("gbp", "GBP"), ("£", "GBP")]:
        if sym in seg:
            currency = cur
            break
    return Match(value=number, raw_text=text[max(0, m.start() - 30):m.end() + 30].strip(),
                 confidence=0.96, extra={"currency": currency})


def extract_price(text: str) -> Optional[Match]:
    """Extract a price amount (used for supplier quotes).

    The first numeric match in a document is often an id/header value, so every
    candidate is scanned and only those with a price-like *label on the same
    line* count. This deliberately rejects bare numbers that merely fall near a
    'Unit Price' table header (e.g. a quantity line '1846 m'), so missing prices
    stay missing instead of being invented.
    """
    _LABELS = ("price", "total", "amount", "quote", "offer", "rate", "value", "budget", "cost")
    best: Optional[tuple[float, str, str]] = None
    for m in _PRICE_RE.finditer(text):
        number = parse_number(f"{m.group('num')} {m.group('mult') or ''}".strip())
        if number is None:
            continue
        num_start = m.start("num")
        line_start = text.rfind("\n", 0, num_start) + 1
        line_ctx = text[line_start:num_start].lower()
        if not any(k in line_ctx for k in _LABELS):
            continue
        seg = text[max(0, num_start - 20):m.end()]
        currency = "INR"
        if re.search(r"€|eur", seg, re.I):
            currency = "EUR"
        elif re.search(r"\$|usd", seg, re.I):
            currency = "USD"
        elif re.search(r"£|gbp", seg, re.I):
            currency = "GBP"
        if best is None or number > best[0]:
            best = (number, currency, seg)
    if best is None:
        return None
    number, currency, seg = best
    return Match(value=number, raw_text=seg.strip(),
                 confidence=0.95, extra={"currency": currency})


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

_DELIVERY_RE = re.compile(
    r"(?:delivery|dispatch|shipment|lead\s*time).{0,50}?"
    r"(?:within|in|under|less\s+than|<)\s*"
    r"(?P<num>\d+)\s*(?P<unit>days?|weeks?|months?|d|wks?)",
    re.IGNORECASE,
)


def extract_delivery(text: str) -> Optional[Match]:
    m = _DELIVERY_RE.search(text)
    if not m:
        return None
    days = delivery_weeks_to_days(f"{m.group('num')} {m.group('unit')}")
    if days is None:
        return None
    return Match(value=days, raw_text=text[max(0, m.start() - 30):m.end() + 30].strip(),
                 confidence=0.96, extra={"unit": m.group("unit").lower()})


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------

_CERT_RE = re.compile(
    r"\b(ISO\s?9001(?:\s?:\s?2015)?|ISO\s?14001(?:\s?:\s?2015)?|ISO\s?45001|"
    r"CE\b|IS\s?13000|API\s?Q1|HACCP|ISI\b|BIS\b|TUV|BEE\b|OHSAS\s?18001)\b",
    re.IGNORECASE,
)


def extract_certifications(text: str) -> list[Match]:
    found: dict[str, Match] = {}
    for m in _CERT_RE.finditer(text):
        name = normalize_certification(m.group(0))
        if name and name not in found:
            found[name] = Match(value=name, raw_text=text[max(0, m.start() - 40):m.end() + 40].strip(),
                                confidence=0.97)
    return list(found.values())


_CERT_VALIDITY_RE = re.compile(
    r"(?:valid\s+(?:up\s+to|until|till)|expir[ye]{1,2}|valid\s+till)\s*:?\s*"
    r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{4}|\d{4})",
    re.IGNORECASE,
)


def extract_cert_validity(text: str) -> Optional[Match]:
    m = _CERT_VALIDITY_RE.search(text)
    if not m:
        return None
    d = parse_date(m.group("date"))
    return Match(value=d, raw_text=text[max(0, m.start() - 40):m.end() + 20].strip(),
                 confidence=0.95)


_ISSUE_DATE_RE = re.compile(
    r"(?:issue\s*(?:date)?|date\s+of\s+issue|issued\s+on)\s*:?\s*"
    r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{4})",
    re.IGNORECASE,
)


def extract_issue_date(text: str) -> Optional[Match]:
    m = _ISSUE_DATE_RE.search(text)
    if not m:
        return None
    d = parse_date(m.group("date"))
    return Match(value=d, raw_text=text[max(0, m.start() - 40):m.end() + 20].strip(),
                 confidence=0.95)


# ---------------------------------------------------------------------------
# Payment / warranty / validity of bid
# ---------------------------------------------------------------------------

_PAYMENT_RE = re.compile(
    r"(?:payment\s*terms?|terms?\s*of\s*payment)\s*:?\s*([^\n\r]{5,80})",
    re.IGNORECASE,
)


def extract_payment_terms(text: str) -> Optional[Match]:
    m = _PAYMENT_RE.search(text)
    if not m:
        return None
    return Match(value=m.group(1).strip().strip(";,"),
                 raw_text=text[max(0, m.start() - 20):m.end() + 20].strip(),
                 confidence=0.9)


_WARRANTY_RE = re.compile(
    r"(?:warranty|guarantee).{0,30}?(?P<num>\d+)\s*(?P<unit>months?|years?|days?)",
    re.IGNORECASE,
)


def extract_warranty(text: str) -> Optional[Match]:
    m = _WARRANTY_RE.search(text)
    if not m:
        return None
    num = parse_number(m.group("num"))
    unit = m.group("unit").lower()
    if "year" in unit:
        num = int(num * 12)
    elif "day" in unit:
        num = None  # too noisy; warranty usually in months
    return Match(value=num, raw_text=text[max(0, m.start() - 30):m.end() + 30].strip(),
                 confidence=0.9)


_BID_VALIDITY_RE = re.compile(
    r"bid\s*(?:validity|valid\s*for|open\s*for)\s*:?\s*(?P<num>\d+)\s*(?P<unit>days?|weeks?)",
    re.IGNORECASE,
)


def extract_bid_validity(text: str) -> Optional[Match]:
    m = _BID_VALIDITY_RE.search(text)
    if not m:
        return None
    return Match(value=parse_number(m.group("num")), raw_text=text[m.start():m.end()].strip(),
                 confidence=0.95)


# ---------------------------------------------------------------------------
# Supplier names
# ---------------------------------------------------------------------------

_SUPPLIER_RE = re.compile(
    r"(?:supplier|vendor|bidder|company|m/s|firm)\s*(?:name)?\s*:?\s*([A-Z][A-Za-z0-9&.\- ]{2,60})",
    re.IGNORECASE,
)


def extract_supplier_name(text: str) -> Optional[Match]:
    m = _SUPPLIER_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip().rstrip(",;")
    return Match(value=name, raw_text=text[max(0, m.start() - 20):m.end()].strip(),
                 confidence=0.9)


# ---------------------------------------------------------------------------
# Requirements wording helpers
# ---------------------------------------------------------------------------

def detect_mandatory(text: str) -> bool:
    seg = text.lower()
    return any(k in seg for k in ["mandatory", "must be", "mandatorily", "shall supply",
                                   "mandatory requirement", "compulsory"])


def detect_unit(text: str) -> Optional[str]:
    m = _QTY_RE.search(text)
    if m:
        return normalize_unit(m.group("unit"))
    return None


def extract_quantity_from_value_line(text: str) -> Optional[Match]:
    """Parse standalone quantity lines like 'Qty : 500 units' or '500 Nos'."""
    return extract_quantity(text)


_AMBIGUOUS_PRICE_NOUNS = ["rate", "unit rate", "per unit", "price per"]


def is_unit_price(text: str) -> bool:
    seg = text.lower()
    return any(k in seg for k in _AMBIGUOUS_PRICE_NOUNS) and "total" not in seg