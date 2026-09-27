"""Typed domain models used by the deterministic verification engine.

These are pure Pydantic models independent of the database layer. They are the
interchange format between extraction agents and the verification engine, and
between the engine and the decision/critic/report layers.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    UNVERIFIED = "UNVERIFIED"


class VerificationOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    UNVERIFIED = "UNVERIFIED"


class Operator(str, Enum):
    EQ = "eq"
    GTE = "gte"
    LTE = "lte"
    CONTAINS = "contains"
    IN = "in"


class RequirementSpec(BaseModel):
    """A single normalized requirement extracted from an RFQ/policy.

    ``field`` values understood by the engine: ``material``, ``quantity``,
    ``price``, ``delivery_days``, ``certification``, ``required_field``,
    ``custom``.
    """

    field: str
    operator: Operator = Operator.EQ
    value: Any
    mandatory: bool = True
    unit: Optional[str] = None
    currency: Optional[str] = None
    tolerance: Optional[float] = None
    label: str = ""
    raw_text: Optional[str] = None
    evidence_id: Optional[str] = None


class CertificationInfo(BaseModel):
    name: str
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    issuer: Optional[str] = None
    certificate_number: Optional[str] = None
    document_id: Optional[str] = None
    evidence_id: Optional[str] = None
    confidence: float = 1.0


class SupplierBid(BaseModel):
    """Normalized supplier quotation used by the verification engine."""

    supplier_name: str
    material: Optional[str] = None
    quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    delivery_days: Optional[int] = None
    delivery_text: Optional[str] = None
    certifications: list[CertificationInfo] = Field(default_factory=list)
    payment_terms: Optional[str] = None
    warranty_months: Optional[int] = None
    bid_validity_days: Optional[int] = None
    compliance_notes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class CheckResult(BaseModel):
    requirement: str
    field: str
    status: CheckStatus = CheckStatus.PASS
    expected: Any = None
    actual: Any = None
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    severity_deduction: float = 0.0


class SupplierEvaluation(BaseModel):
    supplier_name: str
    outcome: VerificationOutcome
    passed: bool
    score: float
    score_breakdown: list[dict] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    mandatory_passed: bool = False
    mandatory_verified: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class VerifierInput(BaseModel):
    """What the verifier needs: requirements + a bid + optional context."""

    case_date: Optional[date] = None
    fx_rates: dict[str, float] = Field(default_factory=dict)  # currency -> INR
    default_currency: str = "INR"


class VerifiedComparison(BaseModel):
    """Result of a deterministic two-way comparison."""
    left: Any = None
    right: Any = None
    comparable: bool = True
    distance: Optional[float] = None
    detail: str = ""