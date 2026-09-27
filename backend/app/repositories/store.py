"""Repository layer: all SQLAlchemy access for cases, documents, requirements, etc.

Keeps session logic out of the API routes and agents. Functions take an explicit
``Session`` (or the module-level ``SessionLocal`` is used by helpers when none is
passed, which is convenient for scripts/agents).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.database.engine import SessionLocal
from backend.app.models.evaluation import EvaluationResult, EvaluationRun
from backend.app.models.evidence import (
    Citation,
    DocumentChunk,
    Evidence,
    ExtractedField,
)
from backend.app.models.execution import (
    AgentExecution,
    ExecutionSpan,
    LLMCall,
    RetrievalLog,
    ToolCall,
)
from backend.app.models.memory import MemoryEntry, MemoryLookupLog
from backend.app.models.procurement import (
    CaseDocument,
    ProcurementCase,
    Quote,
    Recommendation,
    Requirement,
    Supplier,
    SupplierEvaluationRecord,
)


def get_default_session() -> Session:
    return SessionLocal()


# ---------------------------------------------------------------- cases
def create_case(session: Session, *, name: str, description=None, budget=None,
                currency="INR", metadata_json=None, owner_id=None) -> ProcurementCase:
    case = ProcurementCase(
        name=name,
        description=description,
        budget=budget,
        currency=currency,
        metadata_json=metadata_json or {},
        owner_id=owner_id,
        status="draft",
    )
    session.add(case)
    session.flush()
    return case


def get_case(session: Session, case_id: str) -> Optional[ProcurementCase]:
    return session.get(ProcurementCase, case_id)


def get_case_by_bench_id(session: Session, bench_case_id: str) -> Optional[ProcurementCase]:
    """Find an existing case whose metadata carries ``bench_case_id`` (dataset cases)."""
    for case in list_cases(session, limit=5000):
        if case.metadata_json.get("bench_case_id") == bench_case_id:
            return case
    return None


def list_cases(session: Session, limit: int = 100) -> list[ProcurementCase]:
    stmt = select(ProcurementCase).order_by(ProcurementCase.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def set_case_status(session: Session, case_id: str, status: str) -> None:
    case = get_case(session, case_id)
    if case:
        case.status = status


# ---------------------------------------------------------------- documents
def create_document(session: Session, *, case_id: str, filename: str, storage_path: str,
                    doc_type: str, mime_type: str, file_size: int, sha256: str | None = None,
                    page_count: int | None = None) -> CaseDocument:
    doc = CaseDocument(
        case_id=case_id,
        filename=filename,
        storage_path=storage_path,
        doc_type=doc_type,
        mime_type=mime_type,
        file_size=file_size,
        sha256=sha256,
        page_count=page_count,
        status="uploaded",
    )
    session.add(doc)
    session.flush()
    return doc


def get_document(session: Session, document_id: str) -> Optional[CaseDocument]:
    return session.get(CaseDocument, document_id)


def list_documents(session: Session, case_id: str) -> list[CaseDocument]:
    stmt = (
        select(CaseDocument)
        .where(CaseDocument.case_id == case_id)
        .order_by(CaseDocument.created_at.asc())
    )
    return list(session.execute(stmt).scalars())


def update_document_type(session: Session, document_id: str, doc_type: str) -> None:
    doc = get_document(session, document_id)
    if doc:
        doc.doc_type = doc_type


def document_count(session: Session, case_id: str) -> int:
    return session.execute(
        select(func.count()).select_from(CaseDocument).where(CaseDocument.case_id == case_id)
    ).scalar_one()


def documents_of_type(session: Session, case_id: str, doc_type: str) -> list[CaseDocument]:
    stmt = (
        select(CaseDocument)
        .where(CaseDocument.case_id == case_id, CaseDocument.doc_type == doc_type)
        .order_by(CaseDocument.created_at.asc())
    )
    return list(session.execute(stmt).scalars())


# ---------------------------------------------------------------- suppliers / quotes
def create_supplier(session: Session, *, case_id: str, name: str,
                    source_document_id: str | None = None, metadata_json=None) -> Supplier:
    supplier = Supplier(
        case_id=case_id,
        name=name,
        source_document_id=source_document_id,
        metadata_json=metadata_json or {},
    )
    session.add(supplier)
    session.flush()
    return supplier


def get_supplier(session: Session, supplier_id: str) -> Optional[Supplier]:
    return session.get(Supplier, supplier_id)


def list_suppliers(session: Session, case_id: str) -> list[Supplier]:
    return list(
        session.execute(
            select(Supplier).where(Supplier.case_id == case_id).order_by(Supplier.name)
        ).scalars()
    )


def upsert_quote(session: Session, *, case_id: str, supplier_name: str,
                 supplier_id: str | None, values: dict) -> Quote:
    quote = Quote(case_id=case_id, supplier_name=supplier_name, supplier_id=supplier_id, **values)
    session.add(quote)
    session.flush()
    return quote


def list_quotes(session: Session, case_id: str) -> list[Quote]:
    return list(
        session.execute(
            select(Quote).where(Quote.case_id == case_id).order_by(Quote.supplier_name)
        ).scalars()
    )


def add_requirement(session: Session, *, case_id: str, field: str, operator: str,
                    value, unit=None, currency=None, mandatory=True, tolerance=None,
                    label="", raw_text=None, evidence_document_id=None, page=None) -> Requirement:
    req = Requirement(
        case_id=case_id,
        field=field,
        operator=operator,
        value=value if isinstance(value, dict) else {"v": value},
        unit=unit,
        currency=currency,
        mandatory=mandatory,
        tolerance=tolerance,
        label=label,
        raw_text=raw_text,
        evidence_document_id=evidence_document_id,
        page=page,
    )
    session.add(req)
    session.flush()
    return req


def list_requirements(session: Session, case_id: str) -> list[Requirement]:
    return list(
        session.execute(
            select(Requirement).where(Requirement.case_id == case_id).order_by(Requirement.created_at)
        ).scalars()
    )


def clear_analysis_data(session: Session, case_id: str) -> None:
    """Remove previous analysis artifacts so a re-analysis is idempotent."""
    for model, key in [
        (Requirement, "case_id"),
        (Quote, "case_id"),
        (Supplier, "case_id"),
        (SupplierEvaluationRecord, "case_id"),
        (Recommendation, "case_id"),
        (ExtractedField, "case_id"),
        (Evidence, "case_id"),
        (DocumentChunk, "case_id"),
    ]:
        for row in session.execute(select(model).where(getattr(model, key) == case_id)).scalars():
            session.delete(row)
    session.flush()


# ---------------------------------------------------------------- evaluations / recommendations
def save_evaluation(session: Session, *, case_id: str, supplier_name: str, supplier_id: str | None,
                    passed: bool, score: float, status: str, checks: list, rejection_reasons: list,
                    risks: list) -> SupplierEvaluationRecord:
    rec = SupplierEvaluationRecord(
        case_id=case_id,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        passed=passed,
        score=score,
        status=status,
        checks=checks,
        rejection_reasons=rejection_reasons,
        risks=risks,
    )
    session.add(rec)
    session.flush()
    return rec


def list_evaluations(session: Session, case_id: str) -> list[SupplierEvaluationRecord]:
    return list(
        session.execute(
            select(SupplierEvaluationRecord)
            .where(SupplierEvaluationRecord.case_id == case_id)
            .order_by(SupplierEvaluationRecord.score.desc())
        ).scalars()
    )


def save_recommendation(session: Session, *, case_id: str, recommended_supplier: str | None,
                        overall_score: float, confidence: float, status: str, summary: str | None,
                        reasons: list, rejections: list, unknowns: list, risks: list,
                        ranked_suppliers: list) -> Recommendation:
    rec = Recommendation(
        case_id=case_id,
        recommended_supplier=recommended_supplier,
        overall_score=overall_score,
        confidence=confidence,
        status=status,
        summary=summary,
        reasons=reasons,
        rejections=rejections,
        unknowns=unknowns,
        risks=risks,
        ranked_suppliers=ranked_suppliers,
    )
    session.add(rec)
    session.flush()
    return rec


def get_recommendation(session: Session, case_id: str) -> Optional[Recommendation]:
    return session.execute(
        select(Recommendation)
        .where(Recommendation.case_id == case_id)
        .order_by(Recommendation.created_at.desc())
    ).scalars().first()


def list_latest_recommendations(session: Session) -> dict[str, Recommendation]:
    """Latest recommendation per case (one query; order desc keeps the first)."""
    rows = session.execute(
        select(Recommendation).order_by(Recommendation.created_at.desc())
    ).scalars()
    latest: dict[str, Recommendation] = {}
    for row in rows:
        latest.setdefault(row.case_id, row)
    return latest


# ---------------------------------------------------------------- evidence
def chunk_count(session: Session, case_id: str) -> int:
    """Number of indexed chunk rows for a case (0 => nothing indexed yet)."""
    return session.execute(
        select(func.count()).select_from(DocumentChunk).where(DocumentChunk.case_id == case_id)
    ).scalar_one()


def add_evidence(session: Session, *, case_id: str, document_id: str, document_name: str,
                 field: str, value, text: str, page: int | None = None, section: str | None = None,
                 confidence: float = 1.0, doc_type: str = "other", supplier_id: str | None = None,
                 span_start: int | None = None, span_end: int | None = None) -> Evidence:
    ev = Evidence(
        case_id=case_id,
        document_id=document_id,
        document_name=document_name,
        field=field,
        value=value if isinstance(value, dict) else {"v": value},
        text=text,
        page=page,
        section=section,
        confidence=confidence,
        doc_type=doc_type,
        supplier_id=supplier_id,
        span_start=span_start,
        span_end=span_end,
        source="extraction",
    )
    session.add(ev)
    session.flush()
    return ev


def list_evidence(session: Session, case_id: str, field: str | None = None,
                  supplier_id: str | None = None) -> list[Evidence]:
    stmt = select(Evidence).where(Evidence.case_id == case_id)
    if field:
        stmt = stmt.where(Evidence.field == field)
    if supplier_id:
        stmt = stmt.where(Evidence.supplier_id == supplier_id)
    stmt = stmt.order_by(Evidence.created_at)
    return list(session.execute(stmt).scalars())


def get_evidence(session: Session, evidence_id: str) -> Optional[Evidence]:
    return session.get(Evidence, evidence_id)


def add_citation(session: Session, *, case_id: str, claim: str, field: str,
                 evidence_id: str, verified: bool = True, note: str | None = None) -> Citation:
    cit = Citation(case_id=case_id, claim=claim, field=field, evidence_id=evidence_id,
                   verified=verified, verification_note=note)
    session.add(cit)
    session.flush()
    return cit


def list_citations(session: Session, case_id: str) -> list[Citation]:
    return list(session.execute(
        select(Citation).where(Citation.case_id == case_id)
    ).scalars())


# ---------------------------------------------------------------- audit
def add_agent_execution(session: Session, *, case_id: str, request_id: str, agent: str,
                        run_id: str, status: str = "running", task: dict | None = None) -> AgentExecution:
    rec = AgentExecution(case_id=case_id, request_id=request_id, agent=agent, run_id=run_id,
                         status=status, task=task or {})
    session.add(rec)
    session.flush()
    return rec


def finish_agent_execution(session: Session, execution_id: str, *, status: str,
                           output: dict | None = None, error: str | None = None,
                           duration_ms: int | None = None) -> None:
    rec = session.get(AgentExecution, execution_id)
    if rec:
        rec.status = status
        if output is not None:
            rec.output = output
        if error is not None:
            rec.error = error
        rec.duration_ms = duration_ms


def list_agent_executions(session: Session, case_id: str) -> list[AgentExecution]:
    return list(session.execute(
        select(AgentExecution).where(AgentExecution.case_id == case_id)
        .order_by(AgentExecution.started_at.asc())
    ).scalars())


def add_tool_call(session: Session, *, case_id: str, request_id: str, agent: str, tool_name: str,
                  inputs: dict, outputs: dict, status: str = "ok", error: str | None = None,
                  duration_ms: int | None = None) -> ToolCall:
    rec = ToolCall(case_id=case_id, request_id=request_id, agent=agent, tool_name=tool_name,
                   inputs=inputs, outputs=outputs, status=status, error=error, duration_ms=duration_ms)
    session.add(rec)
    session.flush()
    return rec


def list_tool_calls(session: Session, case_id: str) -> list[ToolCall]:
    return list(session.execute(
        select(ToolCall).where(ToolCall.case_id == case_id).order_by(ToolCall.created_at.asc())
    ).scalars())


def add_llm_call(session: Session, *, case_id: str, request_id: str, agent: str, provider: str,
                 model: str | None, prompt_preview: str | None, response_preview: str | None,
                 prompt_tokens: int | None = None, completion_tokens: int | None = None,
                 duration_ms: int | None = None, error: str | None = None) -> LLMCall:
    rec = LLMCall(case_id=case_id, request_id=request_id, agent=agent, provider=provider,
                  model=model, prompt_preview=prompt_preview, response_preview=response_preview,
                  prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                  duration_ms=duration_ms, error=error)
    session.add(rec)
    session.flush()
    return rec


def list_llm_calls(session: Session, case_id: str) -> list[LLMCall]:
    return list(session.execute(
        select(LLMCall).where(LLMCall.case_id == case_id).order_by(LLMCall.created_at.asc())
    ).scalars())


# ---------------------------------------------------------------- memory
def add_memory_entry(session: Session, *, scope: str, scope_key: str, memory_type: str,
                     content: dict, source_case_id: str | None = None,
                     source_document_id: str | None = None, confidence: float = 1.0) -> MemoryEntry:
    rec = MemoryEntry(scope=scope, scope_key=scope_key, memory_type=memory_type, content=content,
                      source_case_id=source_case_id, source_document_id=source_document_id,
                      confidence=confidence)
    session.add(rec)
    session.flush()
    return rec


def query_memory(session: Session, *, scope: str | None = None, scope_key: str | None = None,
                 memory_type: str | None = None, source_case_id: str | None = None,
                 exclude_source_case_id: str | None = None,
                 limit: int = 20) -> list[MemoryEntry]:
    stmt = select(MemoryEntry).order_by(MemoryEntry.created_at.desc()).limit(limit)
    if scope:
        stmt = stmt.where(MemoryEntry.scope == scope)
    if scope_key:
        stmt = stmt.where(MemoryEntry.scope_key == scope_key)
    if memory_type:
        stmt = stmt.where(MemoryEntry.memory_type == memory_type)
    if source_case_id:
        stmt = stmt.where(MemoryEntry.source_case_id == source_case_id)
    if exclude_source_case_id:
        stmt = stmt.where(MemoryEntry.source_case_id != exclude_source_case_id)
    return list(session.execute(stmt).scalars())


def log_memory_lookup(session: Session, *, case_id: str, scope: str, scope_key: str,
                      hits: list) -> None:
    session.add(MemoryLookupLog(case_id=case_id, scope=scope, scope_key=scope_key, hits=hits))


def delete_memory_for_case(session: Session, source_case_id: str) -> int:
    """Delete derived memory for a case before rewriting it idempotently."""
    rows = session.execute(
        select(MemoryEntry).where(MemoryEntry.source_case_id == source_case_id)
    ).scalars()
    count = 0
    for row in rows:
        session.delete(row)
        count += 1
    session.flush()
    return count


# ---------------------------------------------------------------- evaluation
def create_evaluation_run(session: Session, *, name: str, mode: str) -> EvaluationRun:
    run = EvaluationRun(name=name, mode=mode)
    session.add(run)
    session.flush()
    return run


def finish_evaluation_run(session: Session, run_id: str, *, total_cases: int, passed_cases: int,
                          metrics: dict) -> None:
    run = session.get(EvaluationRun, run_id)
    if run:
        run.total_cases = total_cases
        run.passed_cases = passed_cases
        run.metrics = metrics


def add_evaluation_result(session: Session, *, run_id: str, case_id: str, difficulty_tags: list,
                          expected: dict, actual: dict, metrics: dict, passed: bool,
                          duration_ms: int) -> EvaluationResult:
    rec = EvaluationResult(run_id=run_id, case_id=case_id, difficulty_tags=difficulty_tags,
                           expected=expected, actual=actual, metrics=metrics, passed=passed,
                           duration_ms=duration_ms)
    session.add(rec)
    session.flush()
    return rec


def list_evaluation_runs(session: Session, limit: int = 20) -> list[EvaluationRun]:
    return list(session.execute(
        select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(limit)
    ).scalars())


def list_evaluation_results(session: Session, run_id: str) -> list[EvaluationResult]:
    return list(session.execute(
        select(EvaluationResult).where(EvaluationResult.run_id == run_id)
    ).scalars())


# ---------------------------------------------------------------- spans
def add_span(session: Session, *, case_id: str, request_id: str, span_id: str,
             parent_span_id: str | None, name: str, kind: str = "internal",
             attributes: dict | None = None, status: str = "OK",
             duration_ms: int | None = None) -> ExecutionSpan:
    rec = ExecutionSpan(case_id=case_id, request_id=request_id, span_id=span_id,
                        parent_span_id=parent_span_id, name=name, kind=kind,
                        attributes=attributes or {}, status=status, duration_ms=duration_ms)
    session.add(rec)
    session.flush()
    return rec


def list_spans(session: Session, case_id: str) -> list[ExecutionSpan]:
    return list(session.execute(
        select(ExecutionSpan).where(ExecutionSpan.case_id == case_id).order_by(ExecutionSpan.started_at)
    ).scalars())


def add_retrieval_log(session: Session, *, case_id: str, request_id: str, query: str,
                      filters: dict, hits: list, scores: list, latency_ms: int,
                      metadata_json: dict | None = None) -> None:
    session.add(RetrievalLog(case_id=case_id, request_id=request_id, query=query,
                             filters=filters, hits=hits, scores=scores,
                             metadata_json=metadata_json or {}, latency_ms=latency_ms))


def list_retrieval_logs(session: Session, case_id: str) -> list[RetrievalLog]:
    return list(session.execute(
        select(RetrievalLog).where(RetrievalLog.case_id == case_id).order_by(RetrievalLog.created_at)
    ).scalars())
