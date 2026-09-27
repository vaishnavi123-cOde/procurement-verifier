"""Deterministic Procurement Verification Engine.

Public API:

- ``evaluate_supplier(requirements, bid, context)`` -> SupplierEvaluation
- ``verify_claim(claim_value, evidence_values, field)`` -> (status, reason)
- domain models: ``RequirementSpec``, ``SupplierBid``, ``CertificationInfo``,
  ``SupplierEvaluation``, ``CheckResult``, ``VerifierInput``.
- normalization helpers (material/unit/currency/number/date parsing).
"""

from backend.app.services.verifier.engine import evaluate_supplier, verify_claim
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
    materials_match,
    normalize_certification,
    normalize_currency,
    normalize_material,
    normalize_text,
    normalize_unit,
    parse_date,
    parse_number,
    units_compatible,
)

__all__ = [
    "evaluate_supplier",
    "verify_claim",
    "CertificationInfo",
    "CheckResult",
    "CheckStatus",
    "RequirementSpec",
    "SupplierBid",
    "SupplierEvaluation",
    "VerificationOutcome",
    "VerifierInput",
    "convert_quantity",
    "materials_match",
    "normalize_certification",
    "normalize_currency",
    "normalize_material",
    "normalize_text",
    "normalize_unit",
    "parse_date",
    "parse_number",
    "units_compatible",
]