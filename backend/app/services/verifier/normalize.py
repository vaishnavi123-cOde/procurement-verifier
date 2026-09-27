"""Data normalization utilities for deterministic procurement verification.

All comparison logic works on normalized values. Normalization is intentionally
strict: e.g. ``SS 316`` and ``SS 316L`` remain different materials. Unit and
currency conversion only happens through explicit factor tables or explicit
exchange rates; never guessed by an LLM.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Lowercase, strip, collapse whitespace, drop punctuation (keep decimal points)."""
    if value is None:
        return ""
    value = str(value).strip().lower()
    value = re.sub(r"(?<=\d)\.(?=\d)", "<<DOT>>", value)
    value = re.sub(r"[^\w\s]", "", value)
    value = value.replace("<<DOT>>", ".")
    value = _WS.sub(" ", value)
    return value.strip()


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

#: Alias map (normalized -> canonical normalized form).
MATERIAL_ALIASES: dict[str, str] = {
    "sus316l": "ss316l",
    "sus316": "ss316",
    "aim316l": "ss316l",
    "aisi316l": "ss316l",
    "aisi316": "ss316",
    "aisi304": "ss304",
    "sus304": "ss304",
    "1.4404": "ss316l",
    "316l": "ss316l",
    "304": "ss304",
    "316": "ss316",
    "ss316l": "ss316l",
    "ss316": "ss316",
    "ss304": "ss304",
    "sa240316l": "ss316l",
    "mild steel": "ms",
    "carbon steel": "cs",
    "stainlesssteel304": "ss304",
    "stainlesssteel316": "ss316",
    "stainlesssteel316l": "ss316l",
    "aluminium6061": "al6061",
    "aluminum6061": "al6061",
    "al6061": "al6061",
}


def normalize_material(value: str) -> str:
    """Normalize a material descriptor for strict matching.

    Removes spaces/punctuation entirely so that ``SS 316 L`` == ``SS316L``
    while ``SS316`` != ``SS316L``. Alloy numbers (``1.4404``) and common
    designations (``SUS316L``) resolve through the alias table.
    """
    if value is None:
        return ""
    value = str(value).strip().lower()
    space_free = re.sub(r"\s+", "", value)
    alias = MATERIAL_ALIASES.get(space_free) or MATERIAL_ALIASES.get(value)
    if alias:
        return alias
    value = re.sub(r"[^a-z0-9]", "", space_free)
    value = value.replace("stainlesssteel", "ss")
    return value


def materials_match(a: str, b: str) -> bool:
    return bool(a) and normalize_material(a) == normalize_material(b)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

#: canonical unit -> list of accepted tokens (normalized)
UNIT_ALIASES: dict[str, list[str]] = {
    "units": ["unit", "units", "pcs", "pc", "pieces", "piece", "nos", "no", "ea", "each", "qty", "num", "numbers", "number"],
    "kg": ["kg", "kgs", "kilogram", "kilograms", "kgm"],
    "mt": ["mt", "metricton", "metrictons", "tonne", "tonnes"],
    "m": ["m", "meter", "meters", "metre", "metres"],
    "m2": ["m2", "sqm", "sqmtr", "squaremeter", "squaremeters"],
    "m3": ["m3", "cum", "cubicmeter", "cubicmeters"],
    "barrels": ["barrel", "barrels", "bbl"],
    "litre": ["liter", "liters", "litre", "litres", "l"],
    "sets": ["set", "sets"],
    "rolls": ["roll", "rolls"],
    "meters_run": ["rm", "runmeter", "runmeter", "linear meter", "linear meters"],
}

UNIT_FACTORS: dict[str, float] = {
    "units": 1.0,
    "kg": 1.0,
    "mt": 1000.0,
    "m": 1.0,
    "m2": 1.0,
    "m3": 1.0,
    "barrels": 1.0,
    "litre": 1.0,
    "sets": 1.0,
    "rolls": 1.0,
    "meters_run": 1.0,
}

#: physical family of each canonical unit -> only same-family units convert
UNIT_FAMILIES: dict[str, str] = {
    "units": "count",
    "kg": "mass",
    "mt": "mass",
    "m": "length",
    "meters_run": "length",
    "m2": "area",
    "m3": "volume",
    "litre": "volume",
    "barrels": "count",
    "sets": "count",
    "rolls": "count",
}

_CANONICAL_BY_TOKEN: dict[str, str] = {}
for _canon, _tokens in UNIT_ALIASES.items():
    _CANONICAL_BY_TOKEN[_canon] = _canon
    for _t in _tokens:
        _norm = normalize_text(_t)
        _CANONICAL_BY_TOKEN[_norm] = _canon
        _CANONICAL_BY_TOKEN[_norm.replace(" ", "")] = _canon


def normalize_unit(value: str | None) -> str | None:
    if not value:
        return None
    token = normalize_text(value)
    canonical = _CANONICAL_BY_TOKEN.get(token)
    if canonical is None:
        canonical = _CANONICAL_BY_TOKEN.get(token.replace(" ", ""))
    return canonical


def convert_quantity(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert a quantity between compatible units (same physical family only)."""
    from_canon = normalize_unit(from_unit)
    to_canon = normalize_unit(to_unit)
    if from_canon is None or to_canon is None:
        return None
    if UNIT_FAMILIES.get(from_canon) != UNIT_FAMILIES.get(to_canon):
        return None
    from_factor = UNIT_FACTORS.get(from_canon, 1.0)
    to_factor = UNIT_FACTORS.get(to_canon, 1.0)
    return value * from_factor / to_factor


def units_compatible(a: str | None, b: str | None) -> bool:
    a_c = normalize_unit(a)
    b_c = normalize_unit(b)
    if a_c is None or b_c is None:
        return a == b  # both unknown -> consider compatible only if identical raw
    return UNIT_FAMILIES.get(a_c) == UNIT_FAMILIES.get(b_c)


# ---------------------------------------------------------------------------
# Currencies
# ---------------------------------------------------------------------------

CURRENCY_ALIASES: dict[str, str] = {
    "rs": "INR",
    "inr": "INR",
    "₹": "INR",
    "rupees": "INR",
    "usd": "USD",
    "$": "USD",
    "us$": "USD",
    "eur": "EUR",
    "€": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
    "£": "GBP",
    "aed": "AED",
    "sgd": "SGD",
    "jpy": "JPY",
    "cny": "CNY",
    "aud": "AUD",
    "cad": "CAD",
}


def normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    token = value.strip()
    if token in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[token]
    token_low = token.lower()
    if token_low in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[token_low]
    return token.upper()


# ---------------------------------------------------------------------------
# Numbers / prices
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS = "₹$€£"
_NUMBER = re.compile(
    r"-?[0-9][0-9,]*(?:\.\d+)?|\d+\.\d+"
)
_MULTIPLIERS = {
    "lakh": 100_000,
    "lacs": 100_000,
    "lac": 100_000,
    "crore": 10_000_000,
    "cr": 10_000_000,
    "k": 1_000,
    "thousand": 1_000,
}


def _clean_number_token(s: str) -> float:
    s = s.replace(",", "")
    return float(s)


def parse_number(value: str | None) -> float | None:
    """Parse a loose numeric string such as ``"₹ 8,20,000"``, ``"500 units"``, ``"45 days"``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for sym in _CURRENCY_SYMBOLS:
        text = text.replace(sym, "")
    text = text.replace(" ", "")
    text = text.lower()
    multiplier = 1.0
    for token, factor in _MULTIPLIERS.items():
        if text.endswith(token):
            multiplier = factor
            text = text[: -len(token)]
            break
    m = _NUMBER.search(text)
    if not m:
        return None
    try:
        return _clean_number_token(m.group(0)) * multiplier
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%B %d, %Y",
    "%Y/%m/%d",
)


def parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def delivery_weeks_to_days(value: str | None) -> int | None:
    """Parse delivery commitment text into days (supports weeks/months)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    m = _NUMBER.search(text)
    if not m:
        return None
    n = _clean_number_token(m.group(0))
    if "week" in text:
        return int(n * 7)
    if "month" in text:
        return int(n * 30)
    if "day" in text or text.endswith("d"):
        return int(n)
    return int(n)  # assume days by default


# ---------------------------------------------------------------------------
# Certification names
# ---------------------------------------------------------------------------

def normalize_certification(value: str | None) -> str:
    if value is None:
        return ""
    value = str(value).strip().upper()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^A-Z0-9]", "", value)
    for prefix in ("ISO9001", "ISO14001", "ISO45001", "ISOTS16949", "ISO22000", "ISOHACCP", "HACCP"):
        if value.startswith(prefix):
            return prefix
    return value


def certification_aliases(name: str) -> list[str]:
    """Return equivalent normalized labels for a certification name."""
    n = normalize_certification(name)
    aliases = {n}
    if n == "ISO9001":
        aliases.add("ISO9001:2015")
        aliases.add("ISO90012015")
    if n == "ISO14001":
        aliases.add("ISO14001:2015")
    return list(aliases)