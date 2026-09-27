"""Strongly-typed MCP tool input/output schemas.

Output models mirror the domain data returned by existing repository and
service functions, so an MCP client gets the same shape the REST API exposes
where the concepts overlap (evidence, recommendations, analyses).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from backend.app.schemas.api import AnalysisResultOut

DocType = Literal["rfq", "quote", "spec", "policy", "certificate", "history", "other"]

ALLOWED_DOC_TYPES: tuple[str, ...] = ("rfq", "quote", "spec", "policy", "certificate", "history", "other")


# ---------------------------------------------------------------------------
# get_case
# ---------------------------------------------------------------------------
class RequirementSummary(BaseModel):
    id: str
    field: str
    operator: str
    value: Any = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    mandatory: bool = True
    label: str = ""
    evidence_document_id: Optional[str] = None
    page: Optional[int] = None


class CaseInfo(BaseModel):
    case_id: str
    bench_case_id: Optional[str] = None
    name: str
    status: str
    description: Optional[str] = None
    budget: Optional[float] = None
    currency: str
    created_at: datetime
    updated_at: datetime
    requirement_count: int
    document_count: int
    supplier_count: int
    requirements_summary: list[RequirementSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# list_case_documents
# ---------------------------------------------------------------------------
class DocumentInfo(BaseModel):
    document_id: str
    filename: str
    document_type: str
    supplier_id: Optional[str] = None
    page_count: Optional[int] = None
    file_size: int = 0
    status: str
    created_at: datetime


class DocumentListResult(BaseModel):
    case_id: str
    total: int
    items: list[DocumentInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# search_evidence
# ---------------------------------------------------------------------------
class EvidenceHit(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    doc_type: str
    page: int
    section: Optional[str] = None
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    text: str
    score: float
    rank: int
    source: str = "hybrid-retrieval"


class SearchEvidenceResult(BaseModel):
    case_id: str
    query: str
    hits: list[EvidenceHit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# get_supplier
# ---------------------------------------------------------------------------
class SupplierQuote(BaseModel):
    material: Optional[str] = None
    quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    delivery_days: Optional[int] = None
    payment_terms: Optional[str] = None
    warranty_months: Optional[int] = None
    certifications: list[Any] = Field(default_factory=list)
    status: str = ""

    @classmethod
    def from_quote(cls, q: Any) -> "SupplierQuote":
        return cls(
            material=q.material,
            quantity=q.quantity,
            quantity_unit=q.quantity_unit,
            price=q.price,
            currency=q.currency,
            delivery_days=q.delivery_days,
            payment_terms=q.payment_terms,
            warranty_months=q.warranty_months,
            certifications=q.certifications or [],
            status=q.status,
        )


class SupplierInfo(BaseModel):
    supplier_id: str
    name: str
    case_id: str
    source_document_id: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    quote: Optional[SupplierQuote] = None


# ---------------------------------------------------------------------------
# get_supplier_history
# ---------------------------------------------------------------------------
class SupplierHistoryEntry(BaseModel):
    source_case_id: Optional[str] = None
    memory_type: str
    content: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    created_at: datetime


class SupplierHistoryResult(BaseModel):
    supplier_id: str
    supplier_name: str
    total: int
    entries: list[SupplierHistoryEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# get_requirement
# ---------------------------------------------------------------------------
class RequirementEvidenceRef(BaseModel):
    evidence_id: str
    document_name: str
    page: Optional[int] = None
    section: Optional[str] = None
    field: str
    value: Any = None
    confidence: float = 0.0


class RequirementVerificationStatus(BaseModel):
    supplier_name: str
    supplier_id: Optional[str] = None
    status: str
    score: float
    reason: str = ""


class RequirementInfo(BaseModel):
    requirement_id: str
    case_id: str
    field: str
    operator: str
    expected_value: Any = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    mandatory: bool
    label: str = ""
    raw_text: Optional[str] = None
    evidence_document_id: Optional[str] = None
    page: Optional[int] = None
    document_name: Optional[str] = None
    evidence: list[RequirementEvidenceRef] = Field(default_factory=list)
    verification: list[RequirementVerificationStatus] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# evaluate_supplier
# ---------------------------------------------------------------------------
class RequirementCheck(BaseModel):
    requirement: str
    field: str
    status: str
    expected: Any = None
    actual: Any = None
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class SupplierEvaluationResult(BaseModel):
    case_id: str
    supplier_id: str
    supplier_name: str
    outcome: str
    passed: bool
    mandatory_passed: bool
    score: float
    checks: list[RequirementCheck] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# get_case_decision
# ---------------------------------------------------------------------------
class BlockingIssue(BaseModel):
    code: str
    severity: str
    supplier: Optional[str] = None
    field: Optional[str] = None
    message: str


class RankedSupplier(BaseModel):
    rank: int = 0
    supplier_name: str
    status: str
    score: float = 0.0


class CaseDecision(BaseModel):
    case_id: str
    recommended_supplier: Optional[str] = None
    status: str
    overall_score: float = 0.0
    confidence: float = 0.0
    summary: Optional[str] = None
    reasons: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    rejections: list[dict[str, Any]] = Field(default_factory=list)
    ranked_suppliers: list[RankedSupplier] = Field(default_factory=list)
    decision_status: str
    abstention_reason: Optional[str] = None
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# get_audit_report
# ---------------------------------------------------------------------------
class AuditIssue(BaseModel):
    code: str
    severity: str
    supplier: Optional[str] = None
    field: Optional[str] = None
    requirement: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    message: str


class AuditExecution(BaseModel):
    agent: str
    status: str
    started_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None


class AuditReport(BaseModel):
    case_id: str
    critic_status: str
    evidence_coverage: float
    supported_decisions: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_decisions: list[dict[str, Any]] = Field(default_factory=list)
    citation_errors: list[AuditIssue] = Field(default_factory=list)
    issues: list[AuditIssue] = Field(default_factory=list)
    blocking_issues: list[AuditIssue] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)
    executions: list[AuditExecution] = Field(default_factory=list)
    evidence_count: int = 0


__all__ = [
    "ALLOWED_DOC_TYPES",
    "AnalysisResultOut",
    "AuditExecution",
    "AuditIssue",
    "AuditReport",
    "BlockingIssue",
    "CaseDecision",
    "CaseInfo",
    "DocType",
    "DocumentInfo",
    "DocumentListResult",
    "EvidenceHit",
    "RankedSupplier",
    "RequirementCheck",
    "RequirementEvidenceRef",
    "RequirementInfo",
    "RequirementSummary",
    "RequirementVerificationStatus",
    "SearchEvidenceResult",
    "SupplierEvaluationResult",
    "SupplierHistoryEntry",
    "SupplierHistoryResult",
    "SupplierInfo",
    "SupplierQuote",
]