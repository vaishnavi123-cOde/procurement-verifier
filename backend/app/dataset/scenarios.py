"""Scenario authoring: builds case manifests with *authored* ground truth.

This module implements a dataset-local, self-contained verification model that
validates the authored numbers (price vs budget, quantity vs requirement,
delivery limit, certificate validity, material equality) using universal
business rules. It deliberately does **not** import or reuse the production
decision engine.

Design rule: for every authored ``BidTruth`` we assert the authored outcome is
internally consistent with the authored numbers. This catches authoring bugs at
generation time without leaking expected answers into the production code path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from backend.app.dataset.catalog import Material
from backend.app.dataset.manifests import (
    BidTruth,
    CaseManifest,
    CertTruth,
    GroundTruth,
    PolicyTruth,
    RequirementDT,
    RetrievalTask,
)
from backend.app.dataset.pricing import FX_RATES, convert_to_inr

#: difficulty tag vocabulary (also used by scripts/evaluate.py independently)
TAG_VOCAB = (
    "price_over", "price_ok_foreign", "price_over_foreign", "material_mismatch",
    "quantity_shortfall", "unit_trap", "missing_cert", "expired_cert",
    "no_expiry_cert", "currency_fx", "abstention", "dual_compliant",
    "missing_field", "ambiguous_delivery", "policy_approval",
)

_MATERIAL_ALIASES = {
    "ss304": "ss304", "sus304": "ss304", "stainlesssteel304": "ss304",
    "ss316": "ss316", "sus316": "ss316", "stainlesssteel316": "ss316",
    "ss316l": "ss316l", "sus316l": "ss316l", "stainlesssteel316l": "ss316l",
    "ms": "ms", "mildsteel": "ms",
    "cs": "cs", "carbonsteel": "cs",
    "en8": "en8", "c45": "c45",
    "copper": "copper", "brass": "brass", "al6061": "al6061",
    "gi": "gi", "hdpe": "hdpe",
}


def _norm_material(value: str) -> str:
    return _MATERIAL_ALIASES.get(re.sub(r"[^a-z0-9]", "", value.lower()), re.sub(r"[^a-z0-9]", "", value.lower()))


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


# dataset-local unit conversion (mass / length / area / count)
_UNIT_FAMILY = {
    "kg": "mass", "mt": "mass", "tonne": "mass", "tonnes": "mass",
    "m": "length", "meters": "length", "metres": "length", "metre": "length",
    "m2": "area", "sqm": "area",
    "u": "count", "nos": "count", "pcs": "count", "units": "count", "number": "count",
}
_UNIT_FACTOR = {"mt": 1000.0, "tonne": 1000.0, "tonnes": 1000.0}
_UNIT_CANON = {"tonne": "mt", "tonnes": "mt", "meters": "m", "metres": "m", "metre": "m",
               "sqm": "m2", "u": "nos", "pcs": "nos", "units": "nos", "number": "nos"}


def _canon_unit(u: str | None) -> str | None:
    if not u:
        return None
    t = u.strip().lower()
    return _UNIT_CANON.get(t, t)


def _to_kg_mt(value: float, unit: str | None) -> float:
    c = _canon_unit(unit)
    if c == "mt":
        return value * 1000.0
    if c in ("kg", "g"):
        return value * (1.0 / 1000.0 if c == "g" else 1.0)
    return value


def _convert_qty(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    f, t = _canon_unit(from_unit), _canon_unit(to_unit)
    if f is None or t is None:
        return None
    if t == f:
        return value
    if _UNIT_FAMILY.get(f) != _UNIT_FAMILY.get(t) or not _UNIT_FAMILY.get(f):
        return None
    return value * _UNIT_FACTOR.get(f, 1.0) / _UNIT_FACTOR.get(t, 1.0)


# ---------------------------------------------------------------------------
# Internal consistency checker
# ---------------------------------------------------------------------------

class InconsistentCase(ValueError):
    pass


def _cert_of(bid: BidTruth, name: str) -> CertTruth | None:
    low = re.sub(r"[^a-z0-9]", "", name.lower())
    for c in bid.certs:
        if re.sub(r"[^a-z0-9]", "", c.name.lower()) == low:
            return c
    return None


def assert_consistent(spec: dict, requirements: list[RequirementDT],
                      suppliers: list[BidTruth], case_date: date) -> None:
    """Validate that authored numbers reproduce the authored outcomes."""
    budget = requirements_by_field(requirements).get("price")
    for bid in suppliers:
        cats = set(bid.reason_categories)
        by_field = requirements_by_field(requirements)

        # material
        mat_req = by_field.get("material")
        if mat_req is not None:
            if "material_mismatch" in cats:
                if _norm_material(bid.material) == _norm_material(str(mat_req.value)):
                    raise InconsistentCase(f"{bid.supplier}: material must differ, got {bid.material}")
            elif "missing_field" in cats:
                pass
            else:
                if _norm_material(bid.material) != _norm_material(str(mat_req.value)):
                    raise InconsistentCase(
                        f"{bid.supplier}: material {bid.material} != required {mat_req.value}")

        # quantity
        qty_req = by_field.get("quantity")
        if qty_req is not None and bid.quantity is not None:
            converted = _convert_qty(bid.quantity, bid.quantity_unit, qty_req.unit)
            req_qty = float(qty_req.value)
            if "quantity_shortfall" in cats:
                if converted is not None and converted >= req_qty:
                    raise InconsistentCase(f"{bid.supplier}: shortfall bid is not short ({converted})")
            else:
                if converted is not None and converted < req_qty:
                    raise InconsistentCase(f"{bid.supplier}: quantity {converted} < required {req_qty}")

        # price
        if bid.total_price is not None:
            bid_inr = convert_to_inr(bid.total_price, bid.currency)
            if budget is not None:
                ceiling = float(budget.value)
                if any(k in cats for k in ("price_over", "price_over_foreign")):
                    if bid_inr <= ceiling:
                        raise InconsistentCase(f"{bid.supplier}: over-budget bid is within budget ({bid_inr})")
                else:
                    if bid_inr > ceiling:
                        raise InconsistentCase(f"{bid.supplier}: {bid_inr} exceeds budget {ceiling}")

        # delivery
        del_req = by_field.get("delivery_days")
        if del_req is not None and bid.delivery_days is not None:
            limit = float(del_req.value)
            if "delivery_over" in cats:
                if bid.delivery_days <= limit:
                    raise InconsistentCase(f"{bid.supplier}: delivery not over limit")
            elif "missing_field" not in cats:
                if bid.delivery_days > limit:
                    raise InconsistentCase(f"{bid.supplier}: delivery {bid.delivery_days} > {limit}")

        # certifications
        cert_reqs = [r for r in requirements if r.field == "certification"]
        for creq in cert_reqs:
            found = _cert_of(bid, str(creq.value))
            if any(k in cats for k in ("missing_cert",)):
                if found:
                    raise InconsistentCase(f"{bid.supplier}: expected missing cert, found {found.name}")
            elif "expired_cert" in cats:
                if not found or found.expiry is None or found.expiry >= case_date:
                    raise InconsistentCase(f"{bid.supplier}: expected expired cert, got {found}")
            else:
                if not found:
                    raise InconsistentCase(f"{bid.supplier}: cert {creq.value} expected, absent")


def requirements_by_field(requirements: list[RequirementDT]) -> dict[str, RequirementDT]:
    out: dict[str, RequirementDT] = {}
    for r in requirements:
        if r.field not in out or not r.label.startswith("."):
            out[r.field] = r
    return out


# ---------------------------------------------------------------------------
# Number helpers
# ---------------------------------------------------------------------------

def _register(rng, spec: dict, material: Material):
    lo, hi = material.pad
    quantity = rng.randint(lo, hi)
    unit = material.unit
    if unit == "m" and material.family in ("stainless-steel", "carbon-steel", "gi", "hdpe"):
        quantity = rng.randint(lo, hi)
    budget = round(quantity * material.base_price_inr * 1.35)
    budget = int(round(budget / 100) * 100)
    return {"quantity": quantity, "unit": unit, "budget": budget}


def _bid_price(bid_total_raw: float) -> float:
    return round(bid_total_raw, 2)


def _supplier_token(rng, idx: int) -> str:
    return f"bidder{idx}"


# ---------------------------------------------------------------------------
# Template builder
# ---------------------------------------------------------------------------

def build_case_spec(template: str, material: Material, variant: int,
                    suppliers: list, seed: str, case_date: date) -> dict:
    """Return a raw authoring spec consumed by :func:`build_manifest`."""
    return {
        "template": template,
        "material": material,
        "variant": variant,
        "suppliers": suppliers,
        "seed": seed,
        "case_date": case_date,
    }


def template_titles() -> dict[str, str]:
    return {
        "compliance": "Compliance shortlist",
        "certificate_gauntlet": "Certificate gauntlet",
        "quantity_units": "Quantity and unit discipline",
        "currency_fx": "Foreign currency pricing",
        "unverifiable": "Withhold on unverifiable claims",
        "abstention": "Withhold recommendation",
        "dual_compliant": "Among compliant bidders",
    }


def build_manifest(spec: dict) -> CaseManifest:
    """Turn a raw authoring spec into a fully grounded CaseManifest."""
    template = spec["template"]
    material: Material = spec["material"]
    variant: int = spec["variant"]
    suppliers: list = spec["suppliers"]
    seed = spec["seed"]
    case_date: date = spec["case_date"]

    rng = Rng(seed)
    case_id = f"bench-{variant:03d}"

    qty = rng.randint(material.pad[0], material.pad[1])
    unit = material.unit
    budget = int(round(qty * material.base_price_inr * 1.35 / 100) * 100)
    required_cert = _required_cert(template, rng)
    delivery_limit = rng.choice([21, 30, 45])

    requirements = _requirements_for(template, material, qty, unit, budget,
                                     delivery_limit, required_cert, case_date)

    bids = _bids_for(template, suppliers, material, qty, unit, budget,
                     delivery_limit, required_cert, case_date, rng)

    assert_consistent(spec, requirements, bids, case_date)

    outcomes = {b.supplier: {"status": b.outcome, "reasons": b.reason_categories} for b in bids}
    cert_fields = ["certification", "material", "quantity", "price", "delivery_days"]
    evidence: dict[str, list[str]] = {}
    for b in bids:
        present = []
        for f in cert_fields:
            if f == "material" and b.material:
                present.append(f)
            elif f == "quantity" and b.quantity:
                present.append(f)
            elif f == "price" and b.total_price:
                present.append(f)
            elif f == "delivery_days" and (b.delivery_days or b.delivery_text):
                present.append(f)
            elif f == "certification" and b.certs:
                present.append(f)
        evidence[b.supplier] = present

    expected = _expected_status(template, bids)
    gt = GroundTruth(
        expected_recommendation=expected[0],
        expected_status=expected[1],
        expected_outcomes=outcomes,
        expected_requirements=requirements,
        expected_evidence_fields=evidence,
        expected_abstention=expected[1] != "recommended",
    )

    policy = PolicyTruth(enabled="policy_approval" in _tags_for(template, variant))
    if policy.enabled:
        policy.approval_threshold = int(budget * 0.85)
    policy.mandatory_certs = [required_cert]

    retrieval = _retrieval_tasks(template, material, bids)

    manifest = CaseManifest(
        case_id=case_id,
        title=f"{template_titles()[template]} — {material.name}",
        template=template,
        variant=variant,
        seed=seed,
        material_id=material.id,
        case_date=case_date,
        synthetic=True,
        tags=_tags_for(template, variant),
        authoring_requirements=requirements,
        authoring_suppliers=bids,
        policy=policy,
        ground_truth=gt,
        retrieval_tasks=retrieval,
    )
    for i, t in enumerate(retrieval):
        manifest.retrieval_tasks[i].id = f"retr-{case_id}-{i}"
    return manifest


def _required_cert(template: str, rng) -> str:
    if template == "certificate_gauntlet":
        return rng.choice(["ISO 9001:2015", "ISO 14001:2015"])
    return "ISO 9001:2015"


def _requirements_for(template, material, qty, unit, budget, delivery_limit,
                      required_cert, case_date) -> list[RequirementDT]:
    grade = material.grades[0]
    reqs = [
        RequirementDT(field="material", operator="eq", value=grade,
                      mandatory=True, label="material", source_doc="spec"),
        RequirementDT(field="quantity", operator="gte", value=float(qty), unit=unit,
                      mandatory=True, label="quantity", source_doc="rfq"),
        RequirementDT(field="price", operator="lte", value=float(budget),
                      currency="INR", mandatory=True, label="max_price", source_doc="rfq"),
        RequirementDT(field="delivery_days", operator="lte", value=float(delivery_limit),
                      mandatory=True, label="max_delivery", source_doc="rfq"),
        RequirementDT(field="certification", operator="in", value=[required_cert],
                      mandatory=True, label="certification", source_doc="rfq"),
    ]
    return reqs


def _base_cert(cert_name: str, case_date: date, *, expiry: date | None = None,
               issue: date | None = None, quoted: bool = True) -> CertTruth:
    return CertTruth(
        name=cert_name,
        issue=issue or (case_date - timedelta(days=300)),
        expiry=expiry or (case_date + timedelta(days=365 * 2)),
        quoted=quoted,
        certificated=True,
    )


def _bids_for(template, suppliers, material, qty, unit, budget, delivery_limit,
              required_cert, case_date, rng) -> list[BidTruth]:
    grade = material.grades[0]
    neighbor = material.grades[1] if len(material.grades) > 1 else "MS"
    base = material.base_price_inr
    count = len(suppliers)
    bids: list[BidTruth] = []

    if template == "compliance":
        roles = [
            ("PASS", [], 0.90),
            ("FAIL", ["price_over"], 1.40),
            ("FAIL", ["material_mismatch"], 0.75),
        ]
        for i, (outcome, cats, price_factor) in enumerate(roles[:count]):
            bids.append(_bid(rng, suppliers[i], outcome, cats, material, qty, unit,
                             budget, delivery_limit, required_cert, case_date, price_factor, 0))
    elif template == "certificate_gauntlet":
        roles = [
            ("PASS", [], 0.95),
            ("FAIL", ["expired_cert"], 0.85),
            ("FAIL", ["missing_cert"], 0.62),
        ]
        cert_styles = ["valid", "expired", "absent"]
        for i, (outcome, cats, pf) in enumerate(roles[:count]):
            style = cert_styles[i] if i < 3 else "valid"
            bids.append(_bid(rng, suppliers[i], outcome, cats, material, qty, unit,
                             budget, delivery_limit, required_cert, case_date, pf, i,
                             cert_style=style))
    elif template == "quantity_units":
        roles = [
            ("PASS", [], 0.92),
            ("FAIL", ["quantity_shortfall"], 0.70),
            ("PASS", ["unit_trap"], 0.95),
        ]
        for i, (outcome, cats, pf) in enumerate(roles[:count]):
            bids.append(_bid(rng, suppliers[i], outcome, cats, material, qty, unit,
                             budget, delivery_limit, required_cert, case_date, pf, i,
                             qty_override=0.65 if "quantity_shortfall" in cats else None))
        # fix unit-trap format on third bid (same number, metric-tonnes unit)
        if count >= 3 and unit == "kg":
            trap = bids[2]
            trap.quantity = round(qty / 1000, 2)
            trap.quantity_unit = "MT"
            trap.notes = "Face number equals requirement but unit differs (kg vs MT)."
    elif template == "currency_fx":
        roles = [
            ("PASS", ["price_ok_foreign"], 0.95),
            ("FAIL", ["price_over_foreign"], 1.40),
            ("FAIL", ["missing_cert"], 0.7),
        ]
        for i, (outcome, cats, pf) in enumerate(roles[:count]):
            currency = "USD" if i in (0, 1) else "INR"
            bids.append(_bid(rng, suppliers[i], outcome, cats, material, qty, unit,
                             budget, delivery_limit, required_cert, case_date, pf, i,
                             currency=currency))
    elif template == "unverifiable":
        roles = [
            ("UNVERIFIED", ["missing_price"], 1.0),
            ("UNVERIFIED", ["missing_price", "missing_cert"], 0.9),
            ("UNVERIFIED", ["missing_field"], 0.8),
        ]
        for i, (outcome, cats, pf) in enumerate(roles[:count]):
            bids.append(_bid(rng, suppliers[i], outcome, cats, material, qty, unit,
                             budget, delivery_limit, required_cert, case_date, pf, i))
    elif template == "abstention":
        roles = [
            ("FAIL", ["price_over"], 1.60),
            ("FAIL", ["missing_cert"], 0.65),
            ("FAIL", ["material_mismatch"], 0.55),
        ]
        for i, (outcome, cats, pf) in enumerate(roles[:count]):
            bids.append(_bid(rng, suppliers[i], outcome, cats, material, qty, unit,
                             budget, delivery_limit, required_cert, case_date, pf, i))
    elif template == "dual_compliant":
        roles = [
            ("PASS", [], 0.90),
            ("PASS", [], 0.98),
            ("FAIL", ["expired_cert"], 0.8),
        ]
        for i, (outcome, cats, pf) in enumerate(roles[:count]):
            style = "valid"
            if i == 2:
                style = "expired"
            bids.append(_bid(rng, suppliers[i], outcome, cats, material, qty, unit,
                             budget, delivery_limit, required_cert, case_date, pf, i,
                             cert_style=style))
    else:
        raise ValueError(f"Unknown template {template}")

    return bids[:count]


def _bid(rng, supplier, outcome, cats: list[str], material: Material, qty, unit,
         budget, delivery_limit, required_cert, case_date, price_factor, slot,
         currency: str = "INR", cert_style: str = "valid",
         qty_override: float | None = None) -> BidTruth:
    grade = material.grades[0]
    neighbor = material.grades[1] if len(material.grades) > 1 else "MS"

    bid_qty = qty if qty_override is None else round(qty * qty_override)

    if "missing_price" in cats:
        total = None
    else:
        total = _bid_price(budget * price_factor)
        if currency != "INR":
            total = round(total / FX_RATES[currency], 2)

    if "missing_field" in cats:
        delivery_days = None
        delivery_text = None
        payment_terms = None
    elif "ambiguous_delivery" in cats:
        delivery_days = None
        delivery_text = f"within {max(2, delivery_limit // 14)} to {max(2, delivery_limit // 7)} weeks"
    else:
        delta = rng.randint(1, 9)
        if "delivery_over" in cats:
            delivery_days = delivery_limit + delta
        else:
            delivery_days = max(7, delivery_limit - delta)
        delivery_text = f"within {delivery_days} days"

    certs: list[CertTruth] = []
    if "missing_cert" not in cats:
        certs.append(_base_cert(required_cert, case_date))
        if "expired_cert" in cats or cert_style == "expired":
            certs[0].expiry = case_date - timedelta(days=45)
        if cert_style == "no_expiry":
            certs[0].issue = None
            certs[0].expiry = None

    return BidTruth(
        supplier=supplier.name,
        outcome=outcome,
        reason_categories=cats,
        material=grade if "material_mismatch" not in cats else neighbor,
        quantity=bid_qty,
        quantity_unit=unit,
        total_price=total,
        currency=currency,
        delivery_days=delivery_days,
        delivery_text=delivery_text,
        payment_terms=supplier.payment_terms if "missing_field" not in cats else None,
        bid_validity=supplier.bid_validity,
        warranty_months=12 if outcome == "PASS" else None,
        certs=certs,
        formatting={"price_style": rng.choice(["lakh", "indian", "plain"]), "slot": slot},
    )


def _tags_for(template: str, variant: int) -> list[str]:
    if template == "compliance":
        return ["material_mismatch", "price_over"]
    if template == "certificate_gauntlet":
        return ["expired_cert", "missing_cert"]
    if template == "quantity_units":
        return ["quantity_shortfall", "unit_trap"]
    if template == "currency_fx":
        return ["currency_fx", "price_ok_foreign", "price_over_foreign", "missing_cert"]
    if template == "unverifiable":
        return ["abstention", "insufficient", "missing_field"]
    if template == "abstention":
        return ["abstention", "no_valid"]
    return ["dual_compliant", "expired_cert"]


def _expected_status(template: str, bids: list[BidTruth]) -> tuple[str | None, str]:
    passed = [b for b in bids if b.outcome == "PASS"]
    if template == "abstention" and not passed:
        return None, "no_valid"
    if template == "unverifiable":
        return None, "insufficient"
    if not passed:
        return None, "no_valid"
    if all(b.outcome != "PASS" for b in bids):
        return None, "insufficient"
    bids_sorted = sorted(bids, key=lambda b: (0 if b.outcome == "PASS" else 1, -_score(b)))
    winner = bids_sorted[0]
    return winner.supplier, "recommended"


def _score(bid: BidTruth) -> float:
    if bid.outcome != "PASS":
        return 0.0
    base = 100.0
    base -= len([c for c in bid.certs if c.expiry is None]) * 12
    return round(base, 2)


def _retrieval_tasks(template: str, material: Material, bids: list[BidTruth]) -> list[RetrievalTask]:
    q_docs = [f"quote_{b.supplier.replace(' ', '_').lower()}.pdf" for b in bids]
    tasks = [
        RetrievalTask(query=f"Who quoted for {material.name}?", expected_doc_ids=q_docs),
        RetrievalTask(query="What is the required quantity?", expected_doc_ids=["rfq.pdf"]),
        RetrievalTask(query="What is the maximum budget?", expected_doc_ids=["rfq.pdf"]),
        RetrievalTask(query="Which certifications are mandatory?", expected_doc_ids=["rfq.pdf", "policy.pdf"]),
    ]
    if bids:
        b0 = bids[0]
        tasks.append(RetrievalTask(
            query=f"Delivery commitment of {b0.supplier}",
            expected_doc_ids=[f"quote_{b0.supplier.replace(' ', '_').lower()}.pdf"],
        ))
    return tasks


# ---------------------------------------------------------------------------
# Deterministic RNG
# ---------------------------------------------------------------------------

class Rng:
    """Small deterministic PRNG so the whole dataset is regenerable."""

    def __init__(self, seed: str):
        import hashlib
        self._state = int(hashlib.sha256(seed.encode()).hexdigest(), 16) & 0xFFFFFFFFFFFFFFFF
        self._drand = _Mersenne(self._state)

    def randint(self, lo: int, hi: int) -> int:
        return lo + self._drand() % (hi - lo + 1)

    def choice(self, seq):
        return seq[self._drand() % len(seq)]

    @property
    def state(self) -> int:
        return self._state


class _Mersenne:
    """Deterministic MT19937-style generator (stdlib compatible)."""

    def __init__(self, seed: int):
        self._mt = [0] * 624
        self._index = 624
        self._mt[0] = seed & 0xFFFFFFFF
        for i in range(1, 624):
            self._mt[i] = (1812433253 * (self._mt[i - 1] ^ (self._mt[i - 1] >> 30)) + i) & 0xFFFFFFFF

    def _twist(self):
        for i in range(624):
            y = (self._mt[i] & 0x80000000) + (self._mt[(i + 1) % 624] & 0x7FFFFFFF)
            self._mt[i] = self._mt[(i + 397) % 624] ^ (y >> 1)
            if y & 1:
                self._mt[i] ^= 0x9908B0DF
        self._index = 0

    def __call__(self) -> int:
        if self._index >= 624:
            self._twist()
        y = self._mt[self._index]
        self._index += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y & 0xFFFFFFFF