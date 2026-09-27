"""Critic / verifier agent core.

Deterministically audits a supplier evaluation before a recommendation is
accepted. The critic never recomputes verification facts; it checks that the
final decision is *supported by evidence that actually lives in the case*. It
may PASS, WARN, BLOCK, reduce confidence, or force ABSTAIN — it can never
change a deterministic PASS/FAIL or pick a different winner.

Checks:

- INVALID_RECOMMENDATION  recommended supplier not in the passing set
- UNSUPPORTED_CLAIM       a PASS claim on a (candidate or recommended)
                          supplier has no evidence row for that field
- MISSING_EVIDENCE        recommended supplier's PASS rests on a field with no
                          supplier evidence at all
- CONTRADICTORY_EVIDENCE  multiple distinct values for one (supplier, field)
- CITATION_MISMATCH       evidence exists for (supplier, field) but none of the
                          extracted values support the claim the decision makes
- EXTRACTION_CONFLICT     requirement rows disagree for the same field

``audit()`` returns a structured result: ``status`` (PASS | WARNING | BLOCK),
``evidence_coverage`` (0..1 over the decided claims), the per-claim breakdown
(``supported_decisions`` / ``unsupported_decisions``), ``citation_errors`` and
the flat ``issues`` list.

With ``recommended_supplier`` unset it evaluates *every* passing candidate
(used by the critic node); the decision node re-runs it with the actual
recommendation to obtain its precise blocking set and coverage.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.app.services.verifier.normalize import (
    delivery_weeks_to_days,
    materials_match,
    normalize_certification,
)

_CORE_FIELDS = ("material", "quantity", "price", "delivery_days", "certification")

# Exchange rates used by the deterministic engine when converting foreign bids.
# The critic reuses the same explicit table so a converted claim stays supported
# by its raw source value (e.g. 3282 EUR -> 311790 INR at rate 95).
_FX_RATES = (95.0, 84.0, 110.0, 23.0, 63.0, 1.0)


def _unpack(value: Any) -> Any:
    if isinstance(value, dict) and "v" in value:
        return value["v"]
    return value


def _norm(value: Any) -> Any:
    """Normalize a value for (supplier, field) consistency checks."""
    if value is None:
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return value
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return str(value).strip().lower()


def _value_supports_claim(claim: Any, value: Any, field: str) -> bool:
    """Does one extracted evidence value support the claim the decision makes?

    Numeric claims are accepted when the raw evidence value matches the claimed
    (possibly converted) value under the same explicit factors the deterministic
    engine uses: identity, currency conversion, mt<->kg, weeks/months -> days.
    """
    if claim is None or value is None:
        return False
    if isinstance(value, (list, tuple)):
        return any(_value_supports_claim(claim, v, field) for v in value)
    if isinstance(claim, (list, tuple)):
        return any(_value_supports_claim(c, value, field) for c in claim)
    if field == "material":
        return materials_match(str(claim), str(value))
    if field == "certification":
        return normalize_certification(str(claim)) == normalize_certification(str(value))

    # numeric claims (quantity / price / delivery_days)
    try:
        claimed = float(claim)
    except (TypeError, ValueError):
        return False

    try:
        raw = float(value)
    except (TypeError, ValueError):
        if field == "delivery_days":
            days = delivery_weeks_to_days(str(value))
            return days is not None and abs(days - claimed) < 1.0
        return False

    if abs(raw - claimed) < max(1e-6, abs(claimed) * 1e-6):
        return True
    if claimed == 0.0 or raw == 0.0:
        return False
    ratio = claimed / raw
    if field == "price":
        return any(abs(ratio - rate) < max(1e-6, abs(rate) * 1e-6) for rate in _FX_RATES)
    if field in ("quantity", "delivery_days"):
        return any(abs(ratio - factor) < max(1e-6, abs(factor) * 1e-6)
                   for factor in (1000.0, 1 / 1000.0, 7.0, 1 / 7.0, 30.0, 1 / 30.0))
    return False


def _useful_values(evidence_rows: list) -> list:
    """Non-null normalized evidence values (rows without a value prove nothing)."""
    return [_unpack(e.value) for e in evidence_rows if _unpack(e.value) is not None]


def audit(
    evaluations: list[dict],
    requirement_rows: list,
    evidence_rows: list,
    recommended_supplier: Optional[str] = None,
    *,
    supplier_name_to_id: Optional[dict[str, str]] = None,
) -> dict:
    """Run all critic checks and return a structured, evidence-grounded verdict.

    Arguments mirror what the nodes have in scope: the deterministic evaluation
    dicts, persisted requirement rows and persisted evidence rows. The critic
    never re-reads raw documents and never calls the verifier again.
    """
    issues: list[dict] = []
    passed_names = {
        e["supplier_name"] for e in evaluations if e.get("passed") and e.get("status") == "PASS"
    }

    # 1. INVALID_RECOMMENDATION ---------------------------------------------
    if recommended_supplier is not None and recommended_supplier not in passed_names:
        issues.append(_issue("INVALID_RECOMMENDATION", "blocking", recommended_supplier, None,
                             f"Recommended supplier '{recommended_supplier}' is not in the passing set."))

    # 2-3. claim support: MISSING_EVIDENCE / UNSUPPORTED_CLAIM / CITATION_MISMATCH
    candidates = [recommended_supplier] if recommended_supplier else sorted(passed_names)
    supported_claims: list[dict] = []
    unsupported_claims: list[dict] = []

    for supplier in candidates:
        ev = next((e for e in evaluations if e["supplier_name"] == supplier), None)
        if ev is None:
            continue
        sid = (supplier_name_to_id or {}).get(supplier)

        for check in ev.get("checks", []):
            if check.get("status") != "PASS":
                continue
            field = check.get("field")
            if field not in _CORE_FIELDS:
                continue
            claim = check.get("actual")
            evidence = _evidence_for(evidence_rows, supplier, field, sid)
            decision = {
                "supplier": supplier,
                "supplier_id": sid,
                "field": field,
                "requirement": check.get("requirement"),
                "expected": check.get("expected"),
                "actual": claim,
                "evidence_ids": [e.id for e in evidence],
                "source_documents": sorted({e.document_name for e in evidence}),
            }

            if not evidence:
                decision["reason"] = "no supporting evidence rows for this claim"
                unsupported_claims.append(decision)
            elif claim is None:
                # no claimable value -> cannot prove a mismatch; count as supported
                supported_claims.append(decision)
            elif not _useful_values(evidence):
                # rows exist but carry no value: treated as support-by-existence
                # (the same tolerance the deterministic engine applies to staleness)
                supported_claims.append(decision)
            elif not any(
                _value_supports_claim(claim, _unpack(e.value), field) for e in evidence
            ):
                # evidence exists but its value contradicts / does not support the claim
                decision["reason"] = "evidence values do not support the claimed value"
                unsupported_claims.append(decision)
            else:
                supported_claims.append(decision)

        # MISSING_EVIDENCE summary for the recommended supplier
        if recommended_supplier == supplier and ev.get("passed"):
            evidence_absent = [
                c["field"] for c in ev.get("checks", [])
                if c.get("status") == "PASS" and c.get("field") in _CORE_FIELDS
                and not _evidence_for(evidence_rows, supplier, c["field"], sid)
            ]
            if evidence_absent:
                issues.append(_issue(
                    "MISSING_EVIDENCE", "blocking", supplier, evidence_absent[0],
                    f"Recommendation on '{supplier}' relies on fields with no evidence: "
                    + ", ".join(dict.fromkeys(evidence_absent)) + ".",
                    supplier_id=sid))

    for decision in unsupported_claims:
        evidence_rows_for = _evidence_for(evidence_rows, decision["supplier"],
                                          decision["field"], decision["supplier_id"])
        has_evidence = bool(evidence_rows_for)
        if has_evidence and decision.get("actual") is not None:
            code = "CITATION_MISMATCH"
            message = (
                f"Evidence exists for '{decision['field']}' ({decision['actual']}) on "
                f"'{decision['supplier']}' but none of the extracted values support the claim"
                f" (found {sorted({_norm(_unpack(e.value)) for e in evidence_rows_for if _unpack(e.value) is not None})[:5]})."
            )
        else:
            code = "UNSUPPORTED_CLAIM"
            message = (
                f"PASS claim for '{decision['field']}' ({decision['actual']}) on "
                f"'{decision['supplier']}' has no supporting evidence row.")
        issues.append(_issue(
            code, "blocking", decision["supplier"], decision["field"], message,
            supplier_id=decision["supplier_id"], requirement=decision["requirement"],
            evidence_ids=decision["evidence_ids"],
            source_documents=decision["source_documents"],
            expected=decision["expected"], actual=decision["actual"]))

    # 4. CONTRADICTORY_EVIDENCE ---------------------------------------------
    for (key, field), rows in _group_evidence(evidence_rows).items():
        distinct = {str(_norm(r["value"])) for r in rows if r["value"] is not None}
        if len(distinct) > 1 and field in ("material", "quantity", "price", "delivery_days"):
            issues.append(_issue(
                "CONTRADICTORY_EVIDENCE", "blocking", str(key), field,
                f"Evidence for '{field}' contradicts across "
                f"{len(distinct)} distinct values: " + ", ".join(str(v) for v in sorted(distinct)[:5]),
                evidence_ids=[r["evidence_id"] for r in rows if "evidence_id" in r],
                source_documents=[r["document_name"] for r in rows]))

    # 5. EXTRACTION_CONFLICT -------------------------------------------------
    for field, values in _requirement_conflicts(requirement_rows).items():
        if field not in _CORE_FIELDS:
            continue
        issues.append(_issue(
            "EXTRACTION_CONFLICT", "warning", None, field,
            f"Conflicting extraction for requirement '{field}': " + ", ".join(str(v) for v in values)))

    # ---- aggregate --------------------------------------------------------
    blocking = [i for i in issues if i["severity"] == "blocking"]
    status = "BLOCK" if blocking else ("WARNING" if issues else "PASS")

    total_claims = len(supported_claims) + len(unsupported_claims)
    coverage = round(len(supported_claims) / total_claims, 4) if total_claims else 1.0

    return {
        "status": status,
        "evidence_coverage": coverage,
        "supported_decisions": supported_claims,
        "unsupported_decisions": unsupported_claims,
        "citation_errors": [i for i in issues if i["code"] == "CITATION_MISMATCH"],
        "issues": issues,
        "blocking_issues": blocking,
        "blocked": bool(blocking),
        "codes": sorted({i["code"] for i in issues}),
    }


def _evidence_for(evidence_rows: list, supplier_name: str, field: str, supplier_id: str | None) -> list:
    return [
        ev for ev in evidence_rows
        if ev.field == field and _belongs_to(ev, supplier_name, supplier_id)
    ]


def _belongs_to(ev, supplier_name: str, supplier_id: str | None) -> bool:
    if supplier_id and ev.supplier_id:
        return ev.supplier_id == supplier_id
    name = (ev.document_name or "")
    supplier = (supplier_name or "").strip().lower()
    if not supplier:
        return False
    # tolerant filename matching: "omega_steel_quote.pdf" contains "omega steel"
    normalized_name = name.lower().replace("_", " ").replace("-", " ")
    normalized_supplier = supplier.replace("_", " ").replace("-", " ")
    return normalized_supplier in normalized_name


def _group_evidence(evidence_rows: list) -> dict[tuple[str, str], list]:
    grouped: dict[tuple[str, str], list] = {}
    for ev in evidence_rows:
        key = str(ev.supplier_id or ev.document_name or "")
        if not key:
            continue
        grouped.setdefault((key, ev.field), []).append({
            "value": _unpack(ev.value),
            "document_name": ev.document_name,
            "confidence": ev.confidence,
            "evidence_id": ev.id,
        })
    return grouped


def _requirement_conflicts(requirement_rows: list) -> dict[str, list]:
    conflicts: dict[str, dict] = {}
    for row in requirement_rows:
        conflicts.setdefault(row.field, {})[str(_norm(_unpack(row.value)))] = True
    return {k: sorted(v) for k, v in conflicts.items() if len(v) > 1}


def _issue(code: str, severity: str, supplier: str | None, field: str | None,
           message: str, *, supplier_id: str | None = None,
           requirement: str | None = None, expected: Any = None, actual: Any = None,
           evidence_ids: Optional[list[str]] = None,
           source_documents: Optional[list[str]] = None) -> dict:
    return {
        "code": code,
        "severity": severity,
        "supplier": supplier,
        "supplier_id": supplier_id,
        "field": field,
        "requirement": requirement,
        "expected": expected,
        "actual": actual,
        "evidence_ids": evidence_ids or [],
        "source_documents": source_documents or [],
        "message": message,
    }