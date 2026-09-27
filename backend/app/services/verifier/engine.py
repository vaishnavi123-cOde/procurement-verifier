"""Deterministic Procurement Verification Engine.

This module must stay free of LLM usage. It decides PASS / FAIL / WARNING /
UNVERIFIED for every requirement using pure arithmetic, exact-normalized string
matching, explicit conversion tables and explicit exchange rates only.

Key rules:
- ``SS 304`` does NOT satisfy ``SS 316L`` — no fuzzy "close enough" logic.
- A mandatory requirement that cannot be verified -> UNVERIFIED (disqualifying
  for recommendation, surfaced as an explicit unknown).
- No arithmetic on prices/quantities/dates outside this module.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from backend.app.services.verifier.models import (
    CertificationInfo,
    CheckResult,
    CheckStatus,
    RequirementSpec,
    SupplierBid,
    SupplierEvaluation,
    VerificationOutcome,
    VerifierInput,
)
from backend.app.services.verifier.normalize import (
    convert_quantity,
    delivery_weeks_to_days,
    materials_match,
    normalize_certification,
    normalize_currency,
    normalize_material,
    normalize_unit,
    parse_date,
    units_compatible,
)

_DEDUCTIONS = {CheckStatus.FAIL: 20.0, CheckStatus.UNVERIFIED: 12.0, CheckStatus.WARNING: 6.0}

_CERTIFICATION_VALIDITY_WARNING_DAYS = 30


def _deduction(status: CheckStatus) -> float:
    return _DEDUCTIONS.get(status, 0.0)


def _in_range(value: float, target: float, tolerance: Optional[float]) -> bool:
    if tolerance is None or tolerance == 0:
        return value == target
    lo = target * (1 - tolerance)
    hi = target * (1 + tolerance)
    return lo <= value <= hi


def evaluate_supplier(
    requirements: list[RequirementSpec],
    bid: SupplierBid,
    context: Optional[VerifierInput] = None,
) -> SupplierEvaluation:
    """Evaluate a single supplier bid against all requirements."""
    context = context or VerifierInput()
    checks: list[CheckResult] = []

    for req in requirements:
        handler = _HANDLERS.get(req.field)
        if handler is None:
            checks.append(
                _unhandled_check(req)
            )
            continue
        for res in handler(req, bid, context):
            checks.append(res)

    # ----- outcome + score -------------------------------------------------
    failed = [c for c in checks if c.status == CheckStatus.FAIL]
    unverified = [c for c in checks if c.status == CheckStatus.UNVERIFIED]

    score = 100.0 - sum(_deduction(c.status) for c in checks)
    score = max(0.0, round(score, 2))

    if failed:
        outcome = VerificationOutcome.FAIL
    elif unverified:
        outcome = VerificationOutcome.UNVERIFIED
    else:
        outcome = VerificationOutcome.PASS

    mandatory_checks = [
        c for c in checks if _req_is_mandatory_for(c.requirement, requirements)
    ]
    mandatory_passed = bool(mandatory_checks) and all(
        c.status == CheckStatus.PASS for c in mandatory_checks
    )
    mandatory_verified = bool(mandatory_checks) and not any(
        c.status in (CheckStatus.UNVERIFIED,) for c in mandatory_checks
    )

    rejection_reasons = [c.reason for c in failed]
    unknowns = [c.reason for c in unverified]
    risks = [c.reason for c in checks if c.status == CheckStatus.WARNING]

    score_breakdown = [
        {
            "requirement": c.requirement,
            "status": c.status.value,
            "deduction": _deduction(c.status),
        }
        for c in checks
    ]

    return SupplierEvaluation(
        supplier_name=bid.supplier_name,
        outcome=outcome,
        passed=outcome == VerificationOutcome.PASS,
        score=score,
        score_breakdown=score_breakdown,
        checks=checks,
        mandatory_passed=mandatory_passed,
        mandatory_verified=mandatory_verified,
        rejection_reasons=rejection_reasons,
        unknowns=unknowns,
        risks=risks,
    )


def _req_is_mandatory_for(requirement_name: str, requirements: list[RequirementSpec]) -> bool:
    # requirement names are unique per spec (field + label)
    return any(
        (r.field + ("." + (r.label or "")) if r.label else r.field) == requirement_name
        and r.mandatory
        for r in requirements
    )


def _name(item: RequirementSpec) -> str:
    return f"{item.field}.{item.label}" if item.label else item.field


def _base_check(req: RequirementSpec) -> CheckResult:
    return CheckResult(requirement=_name(req), field=req.field)


# ---------------------------------------------------------------------------
# Field handlers
# ---------------------------------------------------------------------------

def _check_material(req: RequirementSpec, bid: SupplierBid, context: VerifierInput):
    res = _base_check(req)
    res.expected = req.value
    res.actual = bid.material
    if req.evidence_id:
        res.evidence_ids.append(req.evidence_id)
    if not bid.material:
        res.status = CheckStatus.UNVERIFIED
        res.reason = "INSUFFICIENT EVIDENCE: supplier material could not be established from available documents."
        return [res]
    match = materials_match(str(req.value), str(bid.material))
    res.status = CheckStatus.PASS if match else CheckStatus.FAIL
    res.reason = (
        f"Material matches required {req.value}."
        if match
        else f"MATERIAL MISMATCH: supplier offers {bid.material}, required material is {req.value}."
    )
    return [res]


def _check_quantity(req: RequirementSpec, bid: SupplierBid, context: VerifierInput):
    res = _base_check(req)
    res.expected = req.value
    res.actual = bid.quantity
    if bid.quantity is None:
        res.status = CheckStatus.UNVERIFIED
        res.reason = "INSUFFICIENT EVIDENCE: supplier quantity could not be established."
        return [res]

    req_unit = normalize_unit(req.unit)
    bid_unit = normalize_unit(bid.quantity_unit)
    extra_note = ""

    if req_unit and bid_unit and req_unit != bid_unit:
        converted = convert_quantity(bid.quantity, bid.quantity_unit, req.unit)
        if converted is None:
            res.status = CheckStatus.UNVERIFIED
            res.actual = f"{bid.quantity} {bid.quantity_unit or ''}"
            res.reason = (
                f"UNIT MISMATCH: bid quantity unit '{bid.quantity_unit}' is not comparable "
                f"to required unit '{req.unit}'. Quantity cannot be verified."
            )
            return [res]
        actual_value = converted
        res.actual = actual_value
        extra_note = f"(converted from {bid.quantity} {bid.quantity_unit})"
    else:
        actual_value = bid.quantity
        res.actual = bid.quantity

    expected = float(req.value)
    actual = float(actual_value)

    if req.operator.value == "eq":
        passed = _in_range(actual, expected, req.tolerance)
    elif req.operator.value == "gte":
        floor = expected * (1 - (req.tolerance or 0.0)) if req.tolerance else expected
        passed = actual >= floor
    elif req.operator.value == "lte":
        ceiling = expected * (1 + (req.tolerance or 0.0)) if req.tolerance else expected
        passed = actual <= ceiling
    else:
        passed = actual == expected

    res.status = CheckStatus.PASS if passed else CheckStatus.FAIL
    res.reason = (
        f"Quantity {actual_value} meets required {req.operator.value} {expected}."
        if passed
        else f"QUANTITY SHORTFALL: supplier offers {actual_value}, required quantity is {expected}."
    )
    if extra_note:
        res.reason = f"{res.reason} {extra_note}"
    return [res]


def _check_price(req: RequirementSpec, bid: SupplierBid, context: VerifierInput):
    res = _base_check(req)
    res.expected = req.value
    res.actual = bid.price
    if bid.price is None:
        res.status = CheckStatus.UNVERIFIED
        res.reason = "INSUFFICIENT EVIDENCE: supplier price could not be established."
        return [res]

    req_currency = normalize_currency(req.currency or context.default_currency)
    bid_currency = normalize_currency(bid.currency)
    price = bid.price
    res.actual = price
    extra_note = ""

    if req_currency and bid_currency and req_currency != bid_currency:
        rate = context.fx_rates.get(bid_currency)
        if rate:
            price = price * rate  # convert bid into requirement currency
            res.actual = price
            extra_note = f"(converted from {bid_currency} at rate {rate})"
        else:
            res.status = CheckStatus.UNVERIFIED
            res.reason = (
                f"CURRENCY MISMATCH: bid priced in {bid_currency} but required in {req_currency}; "
                "no exchange rate available. Price cannot be verified."
            )
            res.actual = f"{bid.price} {bid_currency}"
            return [res]

    expected = float(req.value)
    if req.operator.value == "lte":
        if req.tolerance is not None:
            passing_ceiling = expected * (1 + req.tolerance)
            if price <= expected:
                res.status = CheckStatus.PASS
                res.reason = f"Price {price} is within budget {expected}."
            elif price <= passing_ceiling:
                res.status = CheckStatus.WARNING
                res.reason = f"Price {price} slightly exceeds budget {expected} within tolerance {req.tolerance}."
            else:
                res.status = CheckStatus.FAIL
                res.reason = f"PRICE VIOLATION: price {price} exceeds maximum allowed {expected}."
        else:
            res.status = CheckStatus.PASS if price <= expected else CheckStatus.FAIL
            res.reason = (
                f"Price {price} is within budget {expected}."
                if price <= expected
                else f"PRICE VIOLATION: price {price} exceeds maximum allowed {expected}."
            )
    else:
        res.status = CheckStatus.PASS if price >= expected else CheckStatus.FAIL
        res.reason = f"Price {price} {'meets' if price >= expected else 'falls below'} minimum {expected}."

    if extra_note:
        res.reason = f"{res.reason} {extra_note}"
    return [res]


def _check_delivery(req: RequirementSpec, bid: SupplierBid, context: VerifierInput):
    res = _base_check(req)
    res.expected = req.value
    res.actual = bid.delivery_days if bid.delivery_days is not None else bid.delivery_text
    days = bid.delivery_days
    if days is None and bid.delivery_text:
        days = delivery_weeks_to_days(bid.delivery_text)
        res.actual = f"{bid.delivery_text} (~{days} days)"
    if days is None:
        res.status = CheckStatus.UNVERIFIED
        res.reason = "INSUFFICIENT EVIDENCE: supplier delivery commitment could not be established."
        return [res]

    expected = float(req.value)
    if req.operator.value == "lte":
        res.status = CheckStatus.PASS if days <= expected else CheckStatus.FAIL
        res.reason = (
            f"Delivery {days} days is within the {expected}-day limit."
            if days <= expected
            else f"DELIVERY VIOLATION: delivery {days} days exceeds maximum {expected} days."
        )
    else:
        res.status = CheckStatus.PASS if days >= expected else CheckStatus.FAIL
        res.reason = f"Delivery {days} days {'meets' if days >= expected else 'falls below'} minimum {expected}."
    return [res]


def _check_certification(req: RequirementSpec, bid: SupplierBid, context: VerifierInput):
    required_names = req.value if isinstance(req.value, list) else [req.value]
    results: list[CheckResult] = []
    for cert_name in required_names:
        res = _base_check(req)
        res.requirement = f"{req.field}.{cert_name}" if not req.label else req.label
        res.expected = cert_name
        found: Optional[CertificationInfo] = None
        targets = {normalize_certification(cert_name)}
        for c in bid.certifications:
            cert_norm = normalize_certification(c.name)
            if cert_norm in targets:
                found = c
                break
        if found is None:
            # Absence of the required certificate is a proven violation when the
            # bid is otherwise complete (full price/quantity/delivery evidence)
            # or the supplier presented other certifications. If the bid already
            # trails other evidence gaps, this is part of a broader unknown and
            # must stay UNVERIFIED, never inferred as non-compliance.
            other_gaps = (
                bid.price is None
                or bid.quantity is None
                or bid.material is None
                or (bid.delivery_days is None and not (bid.delivery_text or ""))
            )
            if bid.certifications or not other_gaps:
                res.status = CheckStatus.FAIL
                res.actual = ", ".join(c.name for c in bid.certifications) or "none found"
                res.reason = (
                    f"MISSING CERTIFICATION: required {cert_name} was not found "
                    f"in supplier documents."
                )
            else:
                res.status = CheckStatus.UNVERIFIED
                res.actual = "none found"
                res.reason = (
                    f"MISSING CERTIFICATION: required {cert_name} not provable "
                    "from supplier documents (no certification evidence available)."
                )
            results.append(res)
            continue

        res.actual = found.name
        res.status = CheckStatus.PASS
        res.reason = f"Required certification {cert_name} found ({found.name})."
        results.append(res)

        # validity sub-check
        validity = CheckResult(requirement=f"certification_validity.{cert_name}", field="certification.validity")
        validity.expected = "valid at evaluation date"
        validity.actual = found.name
        if found.evidence_id:
            validity.evidence_ids.append(found.evidence_id)
        ref_date = context.case_date or date.today()
        if found.expiry_date is not None:
            if found.expiry_date < ref_date:
                validity.status = CheckStatus.FAIL
                validity.reason = f"EXPIRED CERTIFICATION: {found.name} expired on {found.expiry_date.isoformat()}."
            elif found.expiry_date < ref_date + timedelta(days=_CERTIFICATION_VALIDITY_WARNING_DAYS):
                validity.status = CheckStatus.WARNING
                validity.reason = f"Certification {found.name} expires soon ({found.expiry_date.isoformat()})."
            else:
                validity.status = CheckStatus.PASS
                validity.reason = f"Certification {found.name} valid until {found.expiry_date.isoformat()}."
        else:
            validity.status = CheckStatus.WARNING
            validity.reason = f"Certification validity/expiry for {found.name} could not be established from documents."
        results.append(validity)
    return results


def _check_required_field(req: RequirementSpec, bid: SupplierBid, context: VerifierInput):
    """Requirement that a named field is present (operator eq on presence)."""
    field_name = str(req.value)
    res = _base_check(req)
    value = getattr(bid, field_name, None)
    if isinstance(value, list):
        value = value or None
    res.expected = "present"
    res.actual = value
    if value is None:
        res.status = CheckStatus.FAIL
        res.reason = f"MISSING REQUIRED FIELD: '{field_name}' was not provided in the supplier bid."
    else:
        res.status = CheckStatus.PASS
        res.reason = f"Required field '{field_name}' present."
    return [res]


def _check_custom(req: RequirementSpec, bid: SupplierBid, context: VerifierInput):
    res = _base_check(req)
    expected = req.value
    actual = None
    if req.label and req.label in bid.raw:
        actual = bid.raw[req.label]
    elif isinstance(expected, str) and not req.label:
        actual = None
    res.expected = expected
    res.actual = actual
    if actual is None:
        res.status = CheckStatus.UNVERIFIED
        res.reason = f"Cannot verify custom requirement '{req.label or req.field}' from available data."
    else:
        norm_actual = str(actual).strip().lower()
        norm_expected = str(expected).strip().lower()
        res.status = CheckStatus.PASS if norm_actual == norm_expected else CheckStatus.FAIL
        res.reason = (
            f"Custom requirement '{req.label}' satisfied." if res.status == CheckStatus.PASS
            else f"Custom requirement '{req.label}' not satisfied (got '{actual}', required '{expected}')."
        )
    return [res]


def _unhandled_check(req: RequirementSpec):
    res = _base_check(req)
    res.status = CheckStatus.UNVERIFIED
    res.reason = f"Requirement field '{req.field}' is not supported by the verification engine."
    return res


_HANDLERS = {
    "material": _check_material,
    "quantity": _check_quantity,
    "price": _check_price,
    "delivery_days": _check_delivery,
    "certification": _check_certification,
    "required_field": _check_required_field,
    "custom": _check_custom,
}


# ---------------------------------------------------------------------------
# Claim verification (used by the Critic / citation validation)
# ---------------------------------------------------------------------------

def verify_claim(claim_value: Any, evidence_values: list[Any], field: str = "") -> tuple[str, str]:
    """Deterministically check whether a claim is supported by evidence values.

    Returns (status, reason) with status in PASS | FAIL | UNVERIFIED.
    """
    if not evidence_values:
        return "UNVERIFIED", "No evidence found to support the claim."

    if isinstance(claim_value, (int, float)) and all(isinstance(v, (int, float)) for v in evidence_values):
        if any(abs(v - float(claim_value)) < 1e-9 for v in evidence_values):
            return "PASS", f"Claim value {claim_value} matches extracted evidence."
        return "FAIL", f"Claim value {claim_value} contradicts extracted evidence {evidence_values}."

    if isinstance(claim_value, str):
        if field in ("material", "certification", "certification_validity"):
            for v in evidence_values:
                if materials_match(claim_value, str(v)):
                    return "PASS", f"Claim '{claim_value}' matches normalized evidence '{v}'."
            return "FAIL", f"Claim '{claim_value}' does not match evidence {evidence_values}."
        if any(str(v).strip().lower() == claim_value.strip().lower() for v in evidence_values):
            return "PASS", f"Claim '{claim_value}' matches extracted evidence."
        return "FAIL", f"Claim '{claim_value}' is not supported by evidence {evidence_values}."

    if any(v == claim_value for v in evidence_values):
        return "PASS", "Claim matches extracted evidence."
    return "FAIL", f"Claim {claim_value} is not supported by evidence {evidence_values}."