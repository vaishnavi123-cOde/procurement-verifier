"""Price formatting + reference FX for the synthetic benchmark.

These are dataset-level constants. They intentionally mirror the deterministic
engine's conversion table so currency cases behave identically in authoring and
verification, but the benchmark never imports that table.
"""

from __future__ import annotations


def format_inr(value: float) -> str:
    """Render a number using Indian digit grouping, e.g. 1245000 -> '12,45,000'."""
    value = int(round(value))
    s = str(value)
    if len(s) <= 3:
        return s
    last3 = s[-3:]
    rest = s[:-3]
    groups = [rest[max(0, i - 2):i] for i in range(len(rest), 0, -2)][::-1]
    return ",".join(groups + [last3])


def format_amount(value: float, currency: str = "INR") -> str:
    """Human-facing amount string in a chosen currency."""
    if currency == "INR":
        return f"Rs. {format_inr(value)}"
    if currency in ("USD", "EUR", "GBP"):
        sym = {"USD": "$", "EUR": "EUR ", "GBP": "£"}[currency]
        return f"{sym}{value:,.2f}".replace("EUR ", "EUR ")
    return f"{currency} {value:,.2f}"


def format_lakh(value: float) -> str:
    """Render an INR amount in lakh notation, e.g. 4250000 -> '42.5 Lakh'."""
    lakhs = round(value / 100_000, 2)
    return f"{lakhs:g} Lakh"


#: reference FX rates to INR (dataset constant; mirrors the engine in this repo)
FX_RATES: dict[str, float] = {
    "INR": 1.0,
    "USD": 84.0,
    "EUR": 95.0,
    "GBP": 110.0,
}


def convert_to_inr(amount: float, currency: str) -> float:
    rate = FX_RATES.get(currency.upper(), None)
    if rate is None:
        raise ValueError(f"No reference FX rate for {currency}")
    return round(amount * rate, 2)