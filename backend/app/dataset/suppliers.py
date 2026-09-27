"""Synthetic supplier roster.

All suppliers in this roster are **entirely fictional** and clearly flagged as
synthetic. No name, address, or transaction is intended to represent a real
entity.

Deterministic generation: output is fully reproducible given a seed string.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class SupplierProfile:
    name: str
    country: str
    price_factor: float       # multiplier against catalogue base_price
    delivery_days: int
    families: tuple[str, ...]
    base_certs: tuple[str, ...]    # always-hold certs (always valid)
    extra_cert: str | None         # one conditional cert (may or may not hold)
    payment_terms: str
    bid_validity: int
    seed_index: int

    @property
    def name_lower(self) -> str:
        return self.name.lower().strip()


_DOMAINS = ("Industries", "Engineering", "Metals", "Supplies", "Fabricators",
            "Solutions", "Corporation", "Systems", "Technik", "Trading")
_ADJ = ("Vulcan", "Meridian", "Apex", "Sterling", "Prism", "Harbinger",
        "Cobalt", "Titan", "Quantum", "Vega", "Zenith", "Orion", "Falcon",
        "Atlas", "Nova", "Ridge", "Ironwood", "Summit", "Pioneer", "Beacon")


def _stable_name(index: int) -> str:
    adj = _ADJ[index % len(_ADJ)]
    dom = _DOMAINS[(index // len(_ADJ)) % len(_DOMAINS)]
    suffix = f" {chr(ord('A') + index // (len(_ADJ) * len(_DOMAINS)))}" if index >= len(_ADJ) * len(_DOMAINS) else ""
    return f"{adj} {dom}{suffix}"


def _hash_int(seed: str) -> int:
    return int(hashlib.sha256(seed.encode()).hexdigest(), 16)


def _seeded_float(seed: str, lo: float, hi: float) -> float:
    h = (_hash_int(seed) % 10_000) / 10_000
    return round(lo + h * (hi - lo), 3)


_CERT_BANK = ("ISO 9001:2015", "ISO 14001:2015", "ISO 45001:2018",
              "CE", "API Q1", "IS 13000", "TUV", "BEE")

_FAMILIES = (
    ("stainless-steel", "carbon-steel"),
    ("stainless-steel",),
    ("carbon-steel", "brass"),
    ("copper", "aluminium"),
    ("aluminium", "stainless-steel"),
    ("hdpe", "gi"),
    ("brass",),
)


def build_roster(count: int = 36, seed: str = "supplier-v1") -> list[SupplierProfile]:
    """Return a deterministic list of fictional suppliers."""
    roster: list[SupplierProfile] = []
    for i in range(count):
        s = f"{seed}:{i}"
        profile = SupplierProfile(
            name=_stable_name(i),
            country="IN",
            price_factor=_seeded_float(f"{s}:price", 0.80, 1.20),
            delivery_days=int(_seeded_float(f"{s}:deliv", 15, 48)),
            families=_FAMILIES[i % len(_FAMILIES)],
            base_certs=(_CERT_BANK[0],),  # every supplier has ISO 9001
            extra_cert=_CERT_BANK[1 + (i % (len(_CERT_BANK) - 1))],
            payment_terms=f"{int(_seeded_float(f'{s}:pay', 15, 45))} days net",
            bid_validity=int(_seeded_float(f"{s}:validity", 45, 75)),
            seed_index=i,
        )
        roster.append(profile)
    return roster


def get_by_name(roster: Sequence[SupplierProfile], name: str) -> SupplierProfile | None:
    low = name.strip().lower()
    for p in roster:
        if p.name_lower == low:
            return p
    return None