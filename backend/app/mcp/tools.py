"""MCP tool implementations.

Thin adapters over the existing service layer. Each handler resolves its inputs
against the current data model, delegates to an existing service, and re-shapes
the result. No business logic (verification, ranking, retrieval fusion,
analysis orchestration) is reimplemented here.

All case-scoped operations validate that their entity belongs to the requested
case, guaranteeing case isolation at every boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.vector_store import VectorStore
from backend.app.repositories import store
from backend.app.services import analysis
from backend.app.services import case_loader
from backend.app.services import critic as critic_service
from backend.app.services.case_loader import CaseNotFoundError
from backend.app.services.indexing import index_case_documents
from backend.app.services.verifier import RequirementSpec, SupplierBid, VerifierInput, evaluate_supplier
from backend.app.schemas.api import AnalysisResultOut
from backend.app.mcp import schemas
from backend.app.mcp.errors import Code, internal_error, mcp_error


# ---------------------------------------------------------------------------
# shared resolution helpers
# ---------------------------------------------------------------------------

def resolve_case(db: Session, case_id: str):
    """Resolve a case id (dataset ``bench-*`` or stored DB id) to a DB row."""
    try:
        case, _ = case_loader.find_or_create_case(db, case_id)
    except CaseNotFoundError as exc:
        raise mcp_error(Code.CASE_NOT_FOUND, str(exc), {"case_id": case_id}) from exc
    return case


# ---------------------------------------------------------------------------
# TOOL 1: get_case
# ---------------------------------------------------------------------------
def tool_get_case(db: Session, case_id: str) -> schemas.CaseInfo:
    case = resolve_case(db, case_id)
    requirements = store.list_requirements(db, case.id)
    documents = store.list_documents(db, case.id)
    suppliers = store.list_suppliers(db, case.id)
    bench_id = case.metadata_json.get("bench_case_id")
    return schemas.CaseInfo(
        case_id=case.id,
        bench_case_id=str(bench_id) if bench_id else None,
        name=case.name,
        status=case.status,
        description=case.description,
        budget=case.budget,
        currency=case.currency,
        created_at=case.created_at,
        updated_at=case.updated_at,
        requirement_count=len(requirements),
        document_count=len(documents),
        supplier_count=len(suppliers),
        requirements_summary=[
            schemas.RequirementSummary(
                id=r.id, field=r.field, operator=r.operator,
                value=_unwrap(r.value), unit=r.unit, currency=r.currency,
                mandatory=r.mandatory, label=r.label or r.field,
                evidence_document_id=r.evidence_document_id, page=r.page,
            )
            for r in requirements
        ],
    )


# ---------------------------------------------------------------------------
# TOOL 2: list_case_documents
# ---------------------------------------------------------------------------
def tool_list_case_documents(db: Session, case_id: str) -> schemas.DocumentListResult:
    case = resolve_case(db, case_id)
    docs = store.list_documents(db, case.id)
    supplier_by_doc = {s.source_document_id: s.id for s in store.list_suppliers(db, case.id)}
    return schemas.DocumentListResult(
        case_id=case.id,
        total=len(docs),
        items=[
            schemas.DocumentInfo(
                document_id=d.id,
                filename=d.filename,
                document_type=d.doc_type,
                supplier_id=supplier_by_doc.get(d.id),
                page_count=d.page_count,
                file_size=d.file_size,
                status=d.status,
                created_at=d.created_at,
            )
            for d in docs
        ],
    )


# ---------------------------------------------------------------------------
# TOOL 3: search_evidence (reuses the existing HybridRetriever)
# ---------------------------------------------------------------------------
def tool_search_evidence(
    db: Session, case_id: str, query: str,
    supplier_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    top_k: int = 8,
) -> schemas.SearchEvidenceResult:
    case = resolve_case(db, case_id)

    if supplier_id:
        supplier = store.get_supplier(db, supplier_id)
        if supplier is None:
            raise mcp_error(Code.SUPPLIER_NOT_FOUND, f"Supplier '{supplier_id}' not found.",
                            {"supplier_id": supplier_id})
        if supplier.case_id != case.id:
            raise mcp_error(
                Code.SUPPLIER_NOT_IN_CASE,
                f"Supplier '{supplier_id}' belongs to case '{supplier.case_id}', not '{case.id}'.",
                {"supplier_id": supplier_id, "belongs_to_case": supplier.case_id, "case_id": case.id},
            )

    if doc_type:
        if doc_type not in schemas.ALLOWED_DOC_TYPES:
            raise mcp_error(Code.INVALID_PARAM,
                            f"Unknown document_type '{doc_type}'. Must be one of "
                            + ", ".join(schemas.ALLOWED_DOC_TYPES) + ".", {"doc_type": doc_type})
        if not store.documents_of_type(db, case.id, doc_type):
            raise mcp_error(Code.RETRIEVAL_FAILED,
                            f"Case '{case_id}' has no '{doc_type}' documents indexed.",
                            {"case_id": case_id, "doc_type": doc_type})

    try:
        vector_store = VectorStore.from_settings()
        # Ensure chunks exist before retrieving (uses the existing indexing service).
        if not store.chunk_count(db, case.id):
            index_case_documents(db, case.id, vector_store)
        retriever = HybridRetriever(vector_store, db)
        hits = retriever.retrieve(query, case_id=case.id, doc_type=doc_type,
                                  supplier_id=supplier_id, top_k=top_k)
    except Exception as exc:
        raise mcp_error(Code.RETRIEVAL_FAILED, f"Retrieval failed for case '{case_id}'.",
                        {"case_id": case_id, "error_type": type(exc).__name__}) from exc

    supplier_names = {s.id: s.name for s in store.list_suppliers(db, case.id)}
    evidence_hits: list[schemas.EvidenceHit] = []
    for h in hits:
        doc = store.get_document(db, h.document_id)
        evidence_hits.append(schemas.EvidenceHit(
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            document_name=doc.filename if doc else h.document_id,
            doc_type=h.doc_type,
            page=h.page,
            section=h.section,
            supplier_id=h.supplier_id,
            supplier_name=supplier_names.get(h.supplier_id) if h.supplier_id else None,
            text=h.content,
            score=round(float(h.score), 4),
            rank=h.rank,
        ))
    return schemas.SearchEvidenceResult(case_id=case.id, query=query, hits=evidence_hits)


# ---------------------------------------------------------------------------
# TOOL 4: get_supplier
# ---------------------------------------------------------------------------
def tool_get_supplier(db: Session, supplier_id: str) -> schemas.SupplierInfo:
    supplier = store.get_supplier(db, supplier_id)
    if supplier is None:
        raise mcp_error(Code.SUPPLIER_NOT_FOUND, f"Supplier '{supplier_id}' not found.",
                        {"supplier_id": supplier_id})
    quote = next((q for q in store.list_quotes(db, supplier.case_id)
                  if q.supplier_id == supplier.id), None)
    return schemas.SupplierInfo(
        supplier_id=supplier.id,
        name=supplier.name,
        case_id=supplier.case_id,
        source_document_id=supplier.source_document_id,
        metadata_json=supplier.metadata_json or {},
        created_at=supplier.created_at,
        quote=schemas.SupplierQuote.from_quote(quote) if quote else None,
    )


# ---------------------------------------------------------------------------
# TOOL 5: get_supplier_history (reads the existing supplier memory store)
# ---------------------------------------------------------------------------
def tool_get_supplier_history(
    db: Session, supplier_id: str,
    case_id: Optional[str] = None,
    limit: int = 20,
) -> schemas.SupplierHistoryResult:
    supplier = store.get_supplier(db, supplier_id)
    if supplier is None:
        raise mcp_error(Code.SUPPLIER_NOT_FOUND, f"Supplier '{supplier_id}' not found.",
                        {"supplier_id": supplier_id})

    if case_id:
        case = store.get_case(db, case_id)
        if case is None:
            raise mcp_error(Code.CASE_NOT_FOUND, f"Case '{case_id}' not found.", {"case_id": case_id})
        if supplier.case_id != case.id:
            raise mcp_error(
                Code.SUPPLIER_NOT_IN_CASE,
                f"Supplier '{supplier_id}' does not participate in case '{case_id}'.",
                {"supplier_id": supplier_id, "case_id": case_id},
            )

    entries = store.query_memory(db, scope="supplier",
                                 scope_key=supplier.name.strip().lower(), limit=1000)
    if case_id:
        entries = [e for e in entries if str((e.content or {}).get("case_id", "")) == case_id]

    result_entries: list[schemas.SupplierHistoryEntry] = []
    for e in entries[:max(1, min(limit, 500))]:
        result_entries.append(schemas.SupplierHistoryEntry(
            source_case_id=e.source_case_id,
            memory_type=e.memory_type,
            content=e.content or {},
            confidence=e.confidence,
            created_at=e.created_at,
        ))
    return schemas.SupplierHistoryResult(
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        total=len(result_entries),
        entries=result_entries,
    )


# ---------------------------------------------------------------------------
# TOOL 6: get_requirement
# ---------------------------------------------------------------------------
def tool_get_requirement(db: Session, case_id: str, requirement_id: str) -> schemas.RequirementInfo:
    case = resolve_case(db, case_id)
    req = next((r for r in store.list_requirements(db, case.id) if r.id == requirement_id), None)
    if req is None:
        raise mcp_error(Code.REQUIREMENT_NOT_FOUND,
                        f"Requirement '{requirement_id}' not found in case '{case_id}'.",
                        {"requirement_id": requirement_id, "case_id": case_id})

    evidence_refs: list[schemas.RequirementEvidenceRef] = []
    for ev in store.list_evidence(db, case.id, field=req.field)[:20]:
        evidence_refs.append(schemas.RequirementEvidenceRef(
            evidence_id=ev.id, document_name=ev.document_name, page=ev.page,
            section=ev.section, field=ev.field, value=_unwrap(ev.value), confidence=ev.confidence,
        ))

    verification: list[schemas.RequirementVerificationStatus] = []
    for ev_rec in store.list_evaluations(db, case.id):
        for check in ev_rec.checks or []:
            if check.get("field") == req.field:
                verification.append(schemas.RequirementVerificationStatus(
                    supplier_name=ev_rec.supplier_name, supplier_id=ev_rec.supplier_id,
                    status=check.get("status", "UNVERIFIED"), score=ev_rec.score,
                    reason=check.get("reason", ""),
                ))

    doc_name = None
    if req.evidence_document_id:
        doc = store.get_document(db, req.evidence_document_id)
        doc_name = doc.filename if doc else None

    return schemas.RequirementInfo(
        requirement_id=req.id, case_id=case.id, field=req.field, operator=req.operator,
        expected_value=_unwrap(req.value), unit=req.unit, currency=req.currency,
        mandatory=req.mandatory, label=req.label or req.field, raw_text=req.raw_text,
        evidence_document_id=req.evidence_document_id, page=req.page, document_name=doc_name,
        evidence=evidence_refs, verification=verification,
    )


# ---------------------------------------------------------------------------
# TOOL 7: evaluate_supplier (calls the existing deterministic verifier)
# ---------------------------------------------------------------------------
def tool_evaluate_supplier(db: Session, case_id: str, supplier_id: str) -> schemas.SupplierEvaluationResult:
    case = resolve_case(db, case_id)
    supplier = store.get_supplier(db, supplier_id)
    if supplier is None:
        raise mcp_error(Code.SUPPLIER_NOT_FOUND, f"Supplier '{supplier_id}' not found.",
                        {"supplier_id": supplier_id})
    if supplier.case_id != case.id:
        raise mcp_error(Code.SUPPLIER_NOT_IN_CASE,
                        f"Supplier '{supplier_id}' belongs to case '{supplier.case_id}'.",
                        {"supplier_id": supplier_id, "case_id": case.id})

    if not store.list_requirements(db, case.id):
        raise mcp_error(
            Code.ANALYSIS_REQUIRED,
            "Case has no persisted requirements. Run 'run_case_analysis' first "
            "so the extraction results are available to the verifier.",
            {"case_id": case_id, "supplier_id": supplier_id},
        )

    specs = analysis.build_requirement_specs(db, case.id)
    bids = analysis.build_bids(db, case.id)
    bid = next((b for b in bids if b.supplier_name.strip().lower() == supplier.name.strip().lower()), None)
    if bid is None:
        raise mcp_error(Code.SUPPLIER_NOT_EVALUATED,
                        f"No quotation extracted for supplier '{supplier.name}' in case '{case_id}'.",
                        {"supplier_id": supplier_id, "case_id": case_id})

    context = VerifierInput(
        case_date=case.metadata_json.get("case_date") or None,
        default_currency=settings.default_currency,
        fx_rates=analysis.settings_fx_rates(),
    )
    evaluation = evaluate_supplier(specs, bid, context)

    checks: list[schemas.RequirementCheck] = []
    for check in evaluation.checks:
        evidence_ids = [
            e.id for e in store.list_evidence(db, case.id, field=check.field, supplier_id=supplier.id)
        ]
        checks.append(schemas.RequirementCheck(
            requirement=check.requirement, field=check.field, status=check.status.value,
            expected=check.expected, actual=check.actual, reason=check.reason,
            evidence_ids=evidence_ids,
        ))

    return schemas.SupplierEvaluationResult(
        case_id=case.id, supplier_id=supplier.id, supplier_name=supplier.name,
        outcome=evaluation.outcome.value, passed=evaluation.passed,
        mandatory_passed=evaluation.mandatory_passed, score=evaluation.score,
        checks=checks, rejection_reasons=evaluation.rejection_reasons,
        unknowns=evaluation.unknowns, risks=evaluation.risks,
    )


# ---------------------------------------------------------------------------
# TOOL 8: run_case_analysis (invokes the existing LangGraph workflow)
# ---------------------------------------------------------------------------
def tool_run_case_analysis(db: Session, case_id: str) -> AnalysisResultOut:
    from backend.app.graphs.graph import run_analysis  # lazy: avoid heavy imports

    result = run_analysis(db, case_id)
    if result.get("status") != "completed" or result.get("errors"):
        raise mcp_error(Code.ANALYSIS_FAILED, "Analysis pipeline did not complete.",
                        {"case_id": case_id, "status": result.get("status"),
                         "errors": result.get("errors", [])})
    return AnalysisResultOut.model_validate(result)


# ---------------------------------------------------------------------------
# TOOL 9: get_case_decision
# ---------------------------------------------------------------------------
def tool_get_case_decision(db: Session, case_id: str) -> schemas.CaseDecision:
    case = resolve_case(db, case_id)
    rec = store.get_recommendation(db, case.id)
    if rec is None:
        raise mcp_error(Code.NO_DECISION,
                        "No decision has been recorded for this case. Run 'run_case_analysis' first.",
                        {"case_id": case_id})

    decision_status = (
        "RECOMMEND" if rec.status == "recommended"
        else "ABSTAIN" if rec.status == "insufficient"
        else "NO_VALID_SUPPLIER"
    )

    blocking_issues: list[schemas.BlockingIssue] = []
    try:
        evaluations = [
            {"supplier_name": e.supplier_name, "passed": e.passed, "status": e.status, "checks": e.checks}
            for e in store.list_evaluations(db, case.id)
        ]
        requirements = store.list_requirements(db, case.id)
        evidence = store.list_evidence(db, case.id)
        name_to_id = {s.name: s.id for s in store.list_suppliers(db, case.id)}
        verdict = critic_service.audit(evaluations, requirements, evidence,
                                       recommended_supplier=rec.recommended_supplier,
                                       supplier_name_to_id=name_to_id)
        blocking_issues = [
            schemas.BlockingIssue(
                code=i["code"], severity=i["severity"], supplier=i.get("supplier"),
                field=i.get("field"), message=i["message"],
            )
            for i in verdict.get("blocking_issues", [])
        ]
    except Exception:
        blocking_issues = []

    ranked: list[schemas.RankedSupplier] = []
    for r in rec.ranked_suppliers or []:
        ranked.append(schemas.RankedSupplier(
            rank=int(r.get("rank", 0)),
            supplier_name=str(r.get("supplier_name", "")),
            status=str(r.get("status", "")),
            score=float(r.get("score", 0.0)),
        ))

    abstain_reason = None
    if decision_status != "RECOMMEND":
        abstain_reason = rec.summary or (
            "No supplier passes every mandatory requirement." if decision_status == "NO_VALID_SUPPLIER"
            else "Insufficient evidence to make a recommendation."
        )

    return schemas.CaseDecision(
        case_id=case.id,
        recommended_supplier=rec.recommended_supplier,
        status=rec.status,
        overall_score=rec.overall_score,
        confidence=rec.confidence,
        summary=rec.summary,
        reasons=rec.reasons or [],
        unknowns=rec.unknowns or [],
        risks=rec.risks or [],
        rejections=rec.rejections or [],
        ranked_suppliers=ranked,
        decision_status=decision_status,
        abstention_reason=abstain_reason,
        blocking_issues=blocking_issues,
        created_at=rec.created_at,
    )


# ---------------------------------------------------------------------------
# TOOL 10: get_audit_report
# ---------------------------------------------------------------------------
def tool_get_audit_report(db: Session, case_id: str) -> schemas.AuditReport:
    case = resolve_case(db, case_id)
    evaluations = [
        {"supplier_name": e.supplier_name, "passed": e.passed, "status": e.status, "checks": e.checks}
        for e in store.list_evaluations(db, case.id)
    ]
    requirements = store.list_requirements(db, case.id)
    evidence = store.list_evidence(db, case.id)
    name_to_id = {s.name: s.id for s in store.list_suppliers(db, case.id)}

    verdict: dict[str, Any] = {}
    try:
        verdict = critic_service.audit(evaluations, requirements, evidence,
                                       supplier_name_to_id=name_to_id)
    except Exception as exc:
        verdict = {"status": "FAILED", "evidence_coverage": 0.0, "issues": [],
                   "citation_errors": [], "blocking_issues": [],
                   "codes": [], "supported_decisions": [], "unsupported_decisions": [],
                   "_error": str(exc)}

    def to_issue(raw: dict) -> schemas.AuditIssue:
        return schemas.AuditIssue(
            code=raw.get("code", ""), severity=raw.get("severity", ""),
            supplier=raw.get("supplier"), field=raw.get("field"),
            requirement=raw.get("requirement"),
            evidence_ids=raw.get("evidence_ids") or [],
            source_documents=raw.get("source_documents") or [],
            message=raw.get("message", ""),
        )

    executions = [
        schemas.AuditExecution(
            agent=e.agent, status=e.status, started_at=e.started_at,
            duration_ms=e.duration_ms, error=e.error,
        )
        for e in store.list_agent_executions(db, case.id)
    ]

    return schemas.AuditReport(
        case_id=case.id,
        critic_status=verdict.get("status", "FAILED"),
        evidence_coverage=float(verdict.get("evidence_coverage", 0.0)),
        supported_decisions=verdict.get("supported_decisions") or [],
        unsupported_decisions=verdict.get("unsupported_decisions") or [],
        citation_errors=[to_issue(i) for i in verdict.get("citation_errors") or []],
        issues=[to_issue(i) for i in verdict.get("issues") or []],
        blocking_issues=[to_issue(i) for i in verdict.get("blocking_issues") or []],
        codes=verdict.get("codes") or [],
        executions=executions,
        evidence_count=len(evidence),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "v" in value:
        return value["v"]
    return value