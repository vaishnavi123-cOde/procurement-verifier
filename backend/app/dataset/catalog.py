"""Material catalog for the synthetic benchmark.

Only materials whose grades are extractable by the deterministic heuristic
material matcher are included. This keeps the benchmark fair: a supplier's
material claim is always *present in the PDF text* in a parseable form, so a
PASS/FAIL decision depends on verification logic, not on OCR luck.

Prices are synthetic reference anchor prices (INR) used only to derive
plausible budgets and bid totals.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    family: str
    unspsc: str
    grades: tuple[str, ...]
    unit: str
    base_price_inr: float
    pad: tuple[int, int]  # (min, max) typical procurement quantity along its unit
    basis: str


#: UNSPSC segment + class codes (public UNSPSC classification under GS1 UNSPSC).
CATALOG: tuple[Material, ...] = (
    Material("ss316l-pipe", "SS 316L seamless pipe", "stainless-steel", "30203000",
             ("SS 316L", "SS 316"), "m", 1450.0, (100, 2000), "per metre"),
    Material("ss304-pipe", "SS 304 seamless pipe", "stainless-steel", "30203000",
             ("SS 304", "SS 316"), "m", 990.0, (100, 3000), "per metre"),
    Material("ss304-sheet", "SS 304 sheet", "stainless-steel", "30201500",
             ("SS 304",), "m2", 780.0, (50, 1200), "per square metre"),
    Material("ss316-sheet", "SS 316 sheet", "stainless-steel", "30201500",
             ("SS 316", "SS 316L"), "m2", 920.0, (50, 900), "per square metre"),
    Material("cs-pipe", "Carbon steel ERW pipe", "carbon-steel", "30203000",
             ("CS", "MS"), "m", 320.0, (500, 5000), "per metre"),
    Material("ms-plate", "Mild steel plate", "carbon-steel", "30201500",
             ("MS", "CS"), "kg", 58.0, (2000, 20000), "per kg"),
    Material("en8-bar", "EN8 bright round bar", "carbon-steel", "30203007",
             ("EN 8", "C 45"), "kg", 62.0, (1000, 12000), "per kg"),
    Material("c45-shaft", "C45 ground shaft", "carbon-steel", "30203007",
             ("C 45", "EN 8"), "kg", 66.0, (500, 6000), "per kg"),
    Material("copper-wire", "EC grade copper wire", "copper", "23132100",
             ("Copper",), "kg", 640.0, (200, 4000), "per kg"),
    Material("brass-valve", "Brass gate valve DN50", "brass", "40141600",
             ("Brass",), "unit", 1250.0, (20, 600), "per piece"),
    Material("al6061-bar", "Aluminium 6061 T6 bar", "aluminium", "30203006",
             ("AL 6061",), "kg", 290.0, (500, 5000), "per kg"),
    Material("gi-pipe", "Galvanised iron pipe 25 NB", "gi", "30203000",
             ("GI",), "m", 210.0, (500, 4000), "per metre"),
    Material("hdpe-pipe", "HDPE pipe 100 mm PN6", "hdpe", "30213000",
             ("HDPE",), "m", 185.0, (500, 6000), "per metre"),
)

BY_ID: dict[str, Material] = {m.id: m for m in CATALOG}


def material_by_id(material_id: str) -> Material:
    return BY_ID[material_id]


def canonical_grade(material: Material) -> str:
    return material.grades[0]


def neighbor_grade(material: Material) -> str | None:
    """A plausible-but-different grade for mismatch-trap cases."""
    if len(material.grades) > 1:
        return material.grades[1]
    return None