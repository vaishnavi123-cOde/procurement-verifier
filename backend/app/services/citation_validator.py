"""Deterministic citation validation.

Validates that a retrieved evidence item is actually a correct citation for the
claim it supports. This is purely deterministic — no LLM calls.

Checks performed:

1. **case_id match** — evidence belongs to the expected case
2. **document correctness** — evidence comes from a document that carries this field
3. **page correctness** — page metadata matches the expected page
4. **supplier association** — supplier_id matches when a specific supplier is expected
5. **text containment** — the evidence source text actually contains the claimed value

Every check produces a CitationCheck with status VALID / INVALID / UNCERTAIN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.app.models.evidence import Evidence


@dataclass
class CitationCheck:
    field: str
    evidence_id: str
    status: str   # VALID | INVALID | UNCERTAIN
    reason: str
    details: dict = field(default_factory=dict)


def _numbers_in(text: str) -> list[float]:
    """Extract numeric values including Indian lakh/crore shorthand."""
    nums: list[float] = []
    for m in re.finditer(r"-?\d+(?:\.\d+)?(?:\s*(?:Lakh|Crore))?", text.replace(",", ""), re.IGNORECASE):
        raw = m.group(0)
        suffix = 1.0
        if "crore" in raw.lower():
            suffix = 1e7
            raw = re.sub(r"\s*Crore", "", raw, flags=re.IGNORECASE)
        elif "lakh" in raw.lower():
            suffix = 1e5
            raw = re.sub(r"\s*Lakh", "", raw, flags=re.IGNORECASE)
        try:
            nums.append(float(raw) * suffix)
        except ValueError:
            pass
    return nums


def _text_contains_claim(text: str, claim_value: Any, field_name: str) -> bool:
    """Check whether the evidence text actually supports the claimed value.

    Transformation-aware for numeric fields (currency conversion, mt<->kg,
    weeks/months -> days) so a converted claim stays "supported" by a raw source
    value — the same tolerance the deterministic engine applies.
    """
    if claim_value is None:
        return True
    if not text:
        return False
    text_lower = text.lower()
    if isinstance(claim_value, bool):
        return True  # booleans are hard to verify by text
    if isinstance(claim_value, (int, float)):
        num_strs = {str(claim_value), f"{claim_value:.1f}", f"{claim_value:.0f}"}
        if any(s in text_lower for s in num_strs):
            return True
        # transformation-aware: does any number found in the text support the claim?
        from backend.app.services.critic import _value_supports_claim
        for num in _numbers_in(text):
            if _value_supports_claim(claim_value, num, field_name):
                return True
        return False
    if isinstance(claim_value, list):
        return all(_text_contains_claim(text, v, field_name) for v in claim_value if v)
    claim_str = str(claim_value).strip()
    if not claim_str:
        return True
    # exact substring match
    if claim_str.lower() in text_lower:
        return True
    # for materials: normalize and compare (space-insensitive, like materials_match)
    if field_name == "material":
        normalized_claim = re.sub(r"\s+", "", claim_str.lower().strip())
        normalized_text = re.sub(r"\s+", "", text_lower)
        return normalized_claim in normalized_text
    # for certifications: compare against the canonical prefix (ISO 9001:2015 -> iso9001)
    if field_name == "certification":
        from backend.app.services.verifier.normalize import normalize_certification
        canon = normalize_certification(claim_str).lower()
        compact_text = re.sub(r"[^a-z0-9]", "", text_lower)
        return bool(canon) and canon in compact_text
    return False


# Documents that carry specific field types
_FIELD_DOC_TYPES = {
    "material": {"spec", "quote", "rfq"},
    "quantity": {"rfq", "quote"},
    "price": {"quote"},
    "delivery_days": {"quote", "rfq"},
    "certification": {"certificate", "rfq", "policy", "quote"},
    "payment_terms": {"quote"},
    "warranty_months": {"quote"},
    "bid_validity_days": {"quote"},
}


def validate_evidence_claim(
    evidence: Evidence,
    *,
    case_id: str,
    expected_value: Any,
    field_name: str,
    expected_supplier_id: str | None = None,
    expected_doc_type: str | None = None,
    expected_page: int | None = None,
) -> CitationCheck:
    """Validate that an evidence row is a correct citation for the given claim.

    Returns a CitationCheck with status VALID, INVALID, or UNCERTAIN.
    """
    reasons: list[str] = []

    # 1. case_id match
    if evidence.case_id != case_id:
        return CitationCheck(
            field=field_name,
            evidence_id=evidence.id,
            status="INVALID",
            reason="evidence belongs to a different case",
        )

    # 2. document type correctness
    valid_doc_types = _FIELD_DOC_TYPES.get(field_name, set())
    if valid_doc_types and evidence.doc_type not in valid_doc_types:
        reasons.append(
            f"document type '{evidence.doc_type}' unlikely to carry '{field_name}' "
            f"(expected one of {sorted(valid_doc_types)})"
        )

    # 3. page correctness
    if expected_page is not None and evidence.page is not None:
        if evidence.page != expected_page:
            reasons.append(f"page mismatch: expected {expected_page}, got {evidence.page}")

    # 4. supplier association
    if expected_supplier_id is not None:
        if evidence.supplier_id is not None and evidence.supplier_id != expected_supplier_id:
            reasons.append(
                f"supplier mismatch: expected {expected_supplier_id}, got {evidence.supplier_id}"
            )

    # 5. text containment check
    text_ok = _text_contains_claim(evidence.text, expected_value, field_name)
    if not text_ok:
        reasons.append(
            f"evidence text does not contain claimed value '{expected_value}'"
        )

    if not reasons:
        return CitationCheck(
            field=field_name,
            evidence_id=evidence.id,
            status="VALID",
            reason="all checks passed",
        )

    # Hard failures (proven provenance/support errors) -> INVALID.
    # Soft signals only (e.g. a doc-type that is merely unusual for the field)
    # -> UNCERTAIN, since the passage may still be legitimately correct.
    has_hard_failure = (
        "different case" in "; ".join(reasons)
        or "page mismatch" in "; ".join(reasons)
        or "supplier mismatch" in "; ".join(reasons)
        or (text_ok is False)
    )
    status = "INVALID" if has_hard_failure else "UNCERTAIN"

    return CitationCheck(
        field=field_name,
        evidence_id=evidence.id,
        status=status,
        reason="; ".join(reasons),
        details={
            "expected_value": expected_value,
            "evidence_text": evidence.text[:200],
            "evidence_doc_type": evidence.doc_type,
            "evidence_page": evidence.page,
            "evidence_supplier_id": evidence.supplier_id,
        },
    )


def validate_case_citations(
    case_id: str,
    requirements: list[dict],
    evidence_rows: list[Evidence],
    supplier_name_to_id: dict[str, str],
) -> list[CitationCheck]:
    """Validate all evidence citations for a case against requirement claims."""
    checks: list[CitationCheck] = []
    for req in requirements:
        field_name = req.get("field", "")
        req_value = req.get("value")
        if isinstance(req_value, dict) and "v" in req_value:
            req_value = req_value["v"]
        for ev in evidence_rows:
            if ev.field != field_name:
                continue
            check = validate_evidence_claim(
                ev,
                case_id=case_id,
                expected_value=req_value,
                field_name=field_name,
                expected_supplier_id=ev.supplier_id,
            )
            checks.append(check)
    return checks


def validate_critic_claims(
    decisions: list[dict],
    evidence_rows: list[Evidence],
    *,
    case_id: str,
) -> dict:
    """Validate the citations behind a critic's per-claim decisions.

    ``decisions`` is the supported/unsupported claim breakdown produced by
    ``critic.audit()``. Returns a summary:
    ``{"checks": [...], "valid", "invalid", "uncertain", "coverage"}``.

    This is a *non-blocking* companion to the critic: it reports citation
    correctness (correct case/document/page/supplier + text supports the claim)
    without altering the critic's blocking decision.
    """
    evidence_by_id = {ev.id: ev for ev in evidence_rows}
    checks: list[CitationCheck] = []
    for decision in decisions:
        claim = decision.get("actual")
        field_name = decision.get("field", "")
        expected_supplier_id = decision.get("supplier_id")
        expected_page = decision.get("page")
        for evid in decision.get("evidence_ids", []):
            ev = evidence_by_id.get(evid)
            if ev is None:
                checks.append(CitationCheck(
                    field=field_name,
                    evidence_id=evid,
                    status="UNCERTAIN",
                    reason="evidence row not found for citation",
                ))
                continue
            checks.append(validate_evidence_claim(
                ev,
                case_id=case_id,
                expected_value=claim,
                field_name=field_name,
                expected_supplier_id=expected_supplier_id,
                expected_page=expected_page,
            ))
    valid = sum(1 for c in checks if c.status == "VALID")
    invalid = sum(1 for c in checks if c.status == "INVALID")
    uncertain = sum(1 for c in checks if c.status == "UNCERTAIN")
    total = len(checks)
    return {
        "checks": checks,
        "valid": valid,
        "invalid": invalid,
        "uncertain": uncertain,
        "total": total,
        "coverage": round(valid / total, 4) if total else 1.0,
    }


def validate_retrieval_citation(
    chunk_text: str,
    chunk_case_id: str,
    chunk_doc_type: str,
    chunk_page: int | None,
    chunk_supplier_id: str | None,
    *,
    expected_case_id: str,
    expected_value: Any,
    field_name: str,
    expected_supplier_id: str | None = None,
    expected_page: int | None = None,
) -> CitationCheck:
    """Validate a retrieval chunk as a citation for a claim.

    Similar to validate_evidence_claim but works with retrieval chunk data
    instead of Evidence ORM rows. Used by the critic when checking RAG output.
    """
    reasons: list[str] = []

    if chunk_case_id != expected_case_id:
        return CitationCheck(
            field=field_name,
            evidence_id="retrieval_chunk",
            status="INVALID",
            reason="chunk belongs to a different case",
        )

    valid_doc_types = _FIELD_DOC_TYPES.get(field_name, set())
    if valid_doc_types and chunk_doc_type not in valid_doc_types:
        reasons.append(
            f"document type '{chunk_doc_type}' unlikely to carry '{field_name}'"
        )

    if expected_page is not None and chunk_page is not None:
        if chunk_page != expected_page:
            reasons.append(f"page mismatch: expected {expected_page}, got {chunk_page}")

    if expected_supplier_id is not None:
        if chunk_supplier_id is not None and chunk_supplier_id != expected_supplier_id:
            reasons.append(
                f"supplier mismatch: expected {expected_supplier_id}, got {chunk_supplier_id}"
            )

    text_ok = _text_contains_claim(chunk_text, expected_value, field_name)
    if not text_ok:
        reasons.append(
            f"chunk text does not contain claimed value '{expected_value}'"
        )

    if not reasons:
        return CitationCheck(
            field=field_name,
            evidence_id="retrieval_chunk",
            status="VALID",
            reason="all checks passed",
        )

    has_hard_failure = text_ok is False or any(
    "different case" in r or "page mismatch" in r or "supplier mismatch" in r
    for r in reasons
)
    status = "INVALID" if has_hard_failure else "UNCERTAIN"

    return CitationCheck(
        field=field_name,
        evidence_id="retrieval_chunk",
        status=status,
        reason="; ".join(reasons),
    )
