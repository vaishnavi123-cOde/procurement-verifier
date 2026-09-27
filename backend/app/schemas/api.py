"""Pydantic API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------- Cases ----------------
class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    budget: Optional[float] = None
    currency: str = "INR"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CaseOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: str
    budget: Optional[float] = None
    currency: str
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CaseList(BaseModel):
    items: list[CaseOut]
    total: int


# ---------------- Documents ----------------
class DocumentOut(BaseModel):
    id: str
    case_id: str
    filename: str
    doc_type: str = "other"
    mime_type: str
    file_size: int
    page_count: Optional[int] = None
    status: str
    error: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int


class DocumentTypeUpdate(BaseModel):
    doc_type: str = Field(..., pattern="^(rfq|quote|spec|policy|certificate|history|other)$")


# ---------------- Requirements / Quotes ----------------
class RequirementOut(BaseModel):
    id: str
    field: str
    operator: str
    value: Any
    unit: Optional[str] = None
    currency: Optional[str] = None
    mandatory: bool
    label: str = ""
    raw_text: Optional[str] = None
    evidence_document_id: Optional[str] = None
    page: Optional[int] = None


class QuoteOut(BaseModel):
    id: str
    supplier_id: Optional[str]
    supplier_name: str
    source_document_ids: list[str]
    material: Optional[str] = None
    quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    delivery_days: Optional[int] = None
    payment_terms: Optional[str] = None
    warranty_months: Optional[int] = None
    certifications: list[Any]
    confidence: float
    status: str


# ---------------- Evidence ----------------
class EvidenceOut(BaseModel):
    id: str
    case_id: str
    document_id: str
    document_name: str
    supplier_id: Optional[str] = None
    doc_type: str
    page: Optional[int] = None
    section: Optional[str] = None
    text: str
    field: str
    value: Any
    confidence: float
    source: str


class CitationOut(BaseModel):
    claim: str
    field: str
    evidence_id: str
    verified: bool
    verification_note: Optional[str] = None
    evidence: Optional[EvidenceOut] = None


# ---------------- Evaluation / Comparison ----------------
class CheckOut(BaseModel):
    requirement: str
    field: str
    status: str
    expected: Any = None
    actual: Any = None
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class SupplierEvaluationOut(BaseModel):
    supplier_id: Optional[str] = None
    supplier_name: str
    passed: bool
    score: float
    status: str
    checks: list[CheckOut]
    rejection_reasons: list[str]
    unknowns: list[str]
    risks: list[str]


# ---------------- Recommendation ----------------
class RecommendationOut(BaseModel):
    id: str
    case_id: str
    recommended_supplier: Optional[str]
    overall_score: float
    confidence: float
    status: str
    summary: Optional[str] = None
    reasons: list[str]
    rejections: list[dict[str, Any]]
    unknowns: list[str]
    risks: list[str]
    ranked_suppliers: list[dict[str, Any]]


class CaseSummaryOut(CaseOut):
    """Case row enriched with its latest recommendation (for list/dashboard)."""

    recommendation: Optional[RecommendationOut] = None


class CaseSummaryList(BaseModel):
    items: list[CaseSummaryOut]
    total: int


# ---------------- Memory ----------------
class MemoryEntryOut(BaseModel):
    id: str
    scope: str
    scope_key: str
    memory_type: str
    content: dict[str, Any]
    source_case_id: Optional[str] = None
    source_document_id: Optional[str] = None
    confidence: float = 0.0
    created_at: Optional[str] = None
    relevance: float = 0.0
    matching_fields: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class SupplierMemoryOut(BaseModel):
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    scope_key: str = ""
    records: list[MemoryEntryOut] = Field(default_factory=list)
    performance_records: list[MemoryEntryOut] = Field(default_factory=list)
    historical_prices: dict[str, Any] = Field(default_factory=dict)
    failure_patterns: dict[str, Any] = Field(default_factory=dict)
    provenance: list[dict[str, Any]] = Field(default_factory=list)


class SimilarCasesOut(BaseModel):
    case_id: str
    items: list[MemoryEntryOut] = Field(default_factory=list)
    total: int = 0


class HistoricalContextOut(BaseModel):
    case_id: str
    context_type: str = "HISTORICAL_CONTEXT"
    current_evidence_label: str = "CURRENT_EVIDENCE"
    final_decision_label: str = "FINAL_DECISION"
    authority: dict[str, Any] = Field(default_factory=dict)
    previous_cases: list[dict[str, Any]] = Field(default_factory=list)
    supplier_historical_performance: list[dict[str, Any]] = Field(default_factory=list)
    historical_price_range: dict[str, Any] = Field(default_factory=dict)
    repeated_compliance_issues: list[dict[str, Any]] = Field(default_factory=list)
    similar_cases: list[dict[str, Any]] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)


# ---------------- Analysis ----------------
class AnalysisStart(BaseModel):
    pass


class AnalysisStatus(BaseModel):
    case_id: str
    status: str
    current_step: Optional[str] = None
    progress: float = 0.0
    steps_completed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class NodeRunOut(BaseModel):
    node: str
    status: str
    started_at_ms: int = 0
    duration_ms: int = 0
    error: Optional[str] = None


class CriticIssueOut(BaseModel):
    code: str
    severity: str
    supplier: Optional[str] = None
    supplier_id: Optional[str] = None
    field: Optional[str] = None
    requirement: Optional[str] = None
    expected: Any = None
    actual: Any = None
    evidence_ids: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    message: str


class CitationCheckOut(BaseModel):
    field: str
    evidence_id: str
    status: str                       # VALID | INVALID | UNCERTAIN
    reason: str


class CriticResultOut(BaseModel):
    status: str = "PASS"                       # PASS | WARNING | BLOCK
    blocked: bool = False
    evidence_coverage: float = 1.0
    supported_decisions: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_decisions: list[dict[str, Any]] = Field(default_factory=list)
    citation_errors: list[CriticIssueOut] = Field(default_factory=list)
    citation_checks: list[CitationCheckOut] = Field(default_factory=list)
    issues: list[CriticIssueOut] = Field(default_factory=list)


class AnalysisResultOut(BaseModel):
    case_id: str
    db_case_id: str
    request_id: str
    status: str
    decision_status: str = ""
    duration_ms: int = 0
    requirements: list[Any] = Field(default_factory=list)
    supplier_evaluations: list[SupplierEvaluationOut] = Field(default_factory=list)
    critic_results: list[CriticIssueOut] = Field(default_factory=list)
    critic_blocked: bool = False
    critic_status: str = "PASS"
    evidence_coverage: float = 1.0
    supported_decisions: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_decisions: list[dict[str, Any]] = Field(default_factory=list)
    citation_errors: list[CriticIssueOut] = Field(default_factory=list)
    citation_checks: list[CitationCheckOut] = Field(default_factory=list)
    recommendation: dict[str, Any] = Field(default_factory=dict)
    evidence_count: int = 0
    retrieval_count: int = 0
    historical_context: dict[str, Any] = Field(default_factory=dict)
    memory_write_count: int = 0
    semantic_reasoning: str = "deterministic"
    node_runs: list[NodeRunOut] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    graph: dict[str, Any] = Field(default_factory=dict)


class AgentRunOut(BaseModel):
    id: str
    agent: str
    status: str
    task: dict[str, Any]
    output: dict[str, Any]
    error: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class ToolCallOut(BaseModel):
    id: str
    agent: str
    tool_name: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    status: str
    duration_ms: Optional[int] = None


# ---------------- Health ----------------
class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    vector_store: str
    llm_provider: str


class ReadyOut(BaseModel):
    status: str
    version: str
    environment: str
    checks: dict[str, str] = Field(default_factory=dict)
    llm_provider: str


# ---------------- Executions / observability ----------------
class ExecutionOut(BaseModel):
    id: str
    case_id: str
    request_id: str
    agent: str
    run_id: str
    status: str
    task: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class ToolCallAuditOut(BaseModel):
    id: str
    case_id: str
    request_id: str
    agent: str
    tool_name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    status: str
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime


class SpanOut(BaseModel):
    id: str
    case_id: str
    request_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    name: str
    kind: str = "internal"
    attributes: dict[str, Any] = Field(default_factory=dict)
    status: str = "OK"
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class ExecutionListOut(BaseModel):
    case_id: str
    executions: list[ExecutionOut] = Field(default_factory=list)
    tool_calls: list[ToolCallAuditOut] = Field(default_factory=list)
    spans: list[SpanOut] = Field(default_factory=list)
