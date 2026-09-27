"""LangGraph nodes. Each node orchestrates existing service modules.

Nodes are thin: they move data between the typed state and reusable services
(``services.case_loader``, ``services.extraction``, ``services.analysis``,
``services.verifier``, ``services.indexing``, ``rag``). No verification logic is
reimplemented here; the deterministic engine remains the single source of truth.

Every node returns a *partial* state update (dict) merged back into the shared
``AnalysisState``.
"""

from __future__ import annotations

import time
from typing import Any

from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.agents.llm import get_llm
from backend.app.graphs.state import AnalysisState
from backend.app.models.procurement import CaseDocument
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.vector_store import VectorStore
from backend.app.repositories import store
from backend.app.services import analysis
from backend.app.services import case_loader as svc_case_loader
from backend.app.services import critic as critic_service
from backend.app.services import citation_validator
from backend.app.services import memory as memory_service
from backend.app.services.extraction.analyzer import analyze_document
from backend.app.services.extraction.models import (
    ExtractedRequirement,
    ExtractionResult,
)
from backend.app.services.ingestion import read_page_text
from backend.app.services.indexing import index_case_documents
from backend.app.services.verifier import (
    RequirementSpec,
    SupplierBid,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fatal(state: AnalysisState, node: str, exc: Exception) -> dict:
    return dict(
        errors=[*state.get("errors", []),
                {"node": node, "error": f"{type(exc).__name__}: {exc}"}],
        case_status="failed",
    )


def _documents_by_type(state: AnalysisState) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for doc in state.get("documents", []):
        grouped.setdefault(doc.get("doc_type", "other"), []).append(doc)
    return grouped


def _document_objects(db: Session, state: AnalysisState, doc_type: str) -> list[CaseDocument]:
    ids = {d["id"] for d in _documents_by_type(state).get(doc_type, [])}
    return [d for d in store.list_documents(db, state["db_case_id"]) if d.id in ids]


# ---------------------------------------------------------------------------
# A. CASE LOADER
# ---------------------------------------------------------------------------

def case_loader(state: AnalysisState, db: Session) -> dict:
    """Resolve the external case id to a DB case + dataset directory."""
    case_id = state["case_id"]
    try:
        case, case_dir = svc_case_loader.find_or_create_case(db, case_id)
    except Exception as exc:
        return _fatal(state, "case_loader", exc)

    store.set_case_status(db, case.id, "analyzing")
    db.commit()
    return dict(
        db_case_id=case.id,
        case_dir=str(case_dir) if case_dir else None,
        case_metadata={
            "name": case.name,
            "description": case.description,
            "case_date": case.metadata_json.get("case_date"),
            "tags": case.metadata_json.get("tags", []),
            "template": case.metadata_json.get("template"),
            "bench_case_id": case_id if case_dir else case.metadata_json.get("bench_case_id"),
            "budget": case.budget,
            "currency": case.currency,
        },
        case_status="analyzing",
    )


# ---------------------------------------------------------------------------
# B. DOCUMENT DISCOVERY / CLASSIFICATION
# ---------------------------------------------------------------------------

def document_discovery(state: AnalysisState, db: Session) -> dict:
    """Discover all documents for the case, classify document types, and
    produce lightweight document metadata for state.

    Classification (rfq | quote | spec | policy | certificate | history |
    other) happens during ingestion; dataset PDFs already ingested are reused
    (idempotent), and uploaded (non-dataset) cases are used as-is.
    """
    db_case_id = state.get("db_case_id") or state["case_id"]
    try:
        if state.get("case_dir"):
            directory = Path(state["case_dir"])
            svc_case_loader.ingest_missing_documents(db, db_case_id, directory)
        elif not store.list_documents(db, db_case_id):
            return _fatal(state, "document_discovery",
                          ValueError(f"No documents found for case '{state['case_id']}'."))
    except Exception as exc:
        return _fatal(state, "document_discovery", exc)

    documents = svc_case_loader.list_document_refs(db, db_case_id)
    if not documents:
        return _fatal(state, "document_discovery",
                      ValueError(f"Case '{state['case_id']}' has no discoverable documents."))
    return dict(documents=documents)


# ---------------------------------------------------------------------------
# C. REQUIREMENT ANALYZER
# ---------------------------------------------------------------------------

def _llm_requirements_assist(state: AnalysisState, db: Session) -> list[ExtractedRequirement]:
    """Optional LLM assist. Never invents: every returned requirement must be
    anchored to a real document + page and its value must parse deterministically.
    Returns [] when LLM is unavailable or nothing passed validation.
    """
    llm = get_llm()
    if not llm.available:
        return []

    extras: list[ExtractedRequirement] = []
    for doc in _document_objects(db, state, "rfq") + _document_objects(db, state, "spec"):
        pages = read_page_text(doc)
        for page_no, text in enumerate(pages, start=1):
            data = llm.generate_json(
                "You extract explicit procurement requirements from RFQ text.",
                f"Page {page_no}:\n{text[:3000]}",
                agent="requirement_analyzer",
                case_id=state["db_case_id"],
                request_id=state["request_id"],
            )
            if not data or "requirements" not in data:
                continue
            normalized_page = " ".join(text.split()).lower()
            for item in data.get("requirements") or []:
                if not isinstance(item, dict):
                    continue
                raw = str(item.get("raw_text") or "").strip()
                if not raw or " ".join(raw.split()).lower() not in normalized_page:
                    continue  # not on this page -> reject (no invention)
                field = str(item.get("field") or "").strip()
                if field not in ("material", "quantity", "price", "delivery_days", "certification"):
                    continue
                value = item.get("value")
                try:
                    spec = RequirementSpec(field=field, operator=str(item.get("operator") or "eq"),
                                           value=value, mandatory=True, raw_text=raw)
                except Exception:
                    continue
                extras.append(ExtractedRequirement(
                    spec=spec,
                    provenance={"document_id": doc.id, "document_name": doc.filename,
                                "page": page_no, "text": raw, "method": "llm"},
                    confidence=min(float(item.get("confidence") or 0.7), 0.95),
                ))
    return extras


def requirement_analyzer(state: AnalysisState, db: Session) -> dict:
    """Extract requirements from RFQ/spec documents (provenance preserved)."""
    requirements: list[ExtractedRequirement] = []
    seen: set[tuple] = set()

    def record(req: ExtractedRequirement) -> None:
        spec = req.spec
        key = (spec.field, str(spec.operator.value if hasattr(spec.operator, "value") else spec.operator),
               str(spec.value), spec.unit or "", spec.currency or "")
        if key in seen:
            return
        seen.add(key)
        requirements.append(req)

    for doc in _document_objects(db, state, "rfq") + _document_objects(db, state, "spec"):
        contribution = analyze_document(doc)
        if contribution:
            for req in contribution.requirements:
                record(req)

    llm_added = 0
    try:
        extras = _llm_requirements_assist(state, db)
        for req in extras:
            record(req)
        llm_added = len(extras)
    except Exception as exc:
        # LLM enhancement is best-effort; never fatals the pipeline.
        return dict(requirements=[r.model_dump(mode="json") for r in requirements],
                    warnings=[*state.get("warnings", []),
                              {"node": "requirement_analyzer", "message": f"LLM assist skipped: {exc}"}])

    llm_used = llm_added > 0
    warnings = state.get("warnings", [])
    if llm_used:
        warnings = [*warnings,
                    {"node": "requirement_analyzer", "message": f"LLM added {llm_added} requirements."}]
    return dict(
        requirements=[r.model_dump(mode="json") for r in requirements],
        semantic_reasoning="llm-assisted" if llm_used else "deterministic",
        llm_used=llm_used,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# D. EXTRACTION NODE  (bids, certificates, policy)
# ---------------------------------------------------------------------------

def extraction_node(state: AnalysisState, db: Session) -> dict:
    """Extract supplier bids, certificates and policy clauses from documents."""
    bids: dict[str, dict] = {}
    certificates: list[dict] = []
    policy_clauses: list[dict] = []

    for doc_type in ("quote", "certificate", "policy", "history"):
        for doc in _document_objects(db, state, doc_type):
            contribution = analyze_document(doc)
            if contribution is None:
                continue
            for name, bid in contribution.bids.items():
                bids[name] = bid.model_dump(mode="json")
            certificates.extend(c.model_dump(mode="json") for c in contribution.certifications)
            policy_clauses.extend(c.model_dump(mode="json") for c in contribution.policy_clauses)

    return dict(
        extracted_bids=bids,
        certificates=certificates,
        policy_clauses=policy_clauses,
    )


# ---------------------------------------------------------------------------
# E. EVIDENCE NODE
# ---------------------------------------------------------------------------

def evidence_node(state: AnalysisState, db: Session) -> dict:
    """Merge extraction output and persist it as traceable evidence."""
    try:
        extraction = ExtractionResult.model_validate({
            "requirements": state.get("requirements", []),
            "bids": state.get("extracted_bids", {}),
            "certifications": state.get("certificates", []),
            "policy_clauses": state.get("policy_clauses", []),
        })
        # idempotent re-analysis: drop artifacts from any earlier run before
        # persisting this run's facts (side-effect only; facts are recomputed).
        store.clear_analysis_data(db, state["db_case_id"])
        stats = analysis.persist_extraction(db, state["db_case_id"], extraction)
        analysis.apply_policy(db, state["db_case_id"])
        db.commit()
    except Exception as exc:
        return _fatal(state, "evidence_node", exc)

    evidence = [
        {"id": e.id, "field": e.field, "value": e.value,
         "document_name": e.document_name, "doc_type": e.doc_type,
         "page": e.page, "confidence": e.confidence}
        for e in store.list_evidence(db, state["db_case_id"])
    ]
    return dict(
        extraction=extraction.model_dump(mode="json"),
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# F. MEMORY RETRIEVAL NODE
# ---------------------------------------------------------------------------

def memory_retrieval_node(state: AnalysisState, db: Session) -> dict:
    """Retrieve historical context without feeding it into verification."""
    db_case_id = state["db_case_id"]
    try:
        context = memory_service.build_historical_context(db, db_case_id)
    except Exception as exc:
        return dict(
            historical_context=memory_service.empty_historical_context(db_case_id),
            warnings=[*state.get("warnings", []),
                      {"node": "memory_retrieval", "message": f"memory unavailable: {exc}"}],
        )
    return dict(historical_context=context)


# ---------------------------------------------------------------------------
# G. SUPPLIER EVALUATION NODE
# ---------------------------------------------------------------------------

def supplier_evaluation(state: AnalysisState, db: Session) -> dict:
    """Construct supplier x requirement evaluation inputs.

    Rebuilds every requirement spec from persisted requirements and pairs each
    extracted supplier quote (bids, incl. certification validity) with the
    applicable requirements, producing the exact input surface the deterministic
    verifier consumes. No verification logic lives here.
    """
    db_case_id = state["db_case_id"]
    try:
        extraction = ExtractionResult.model_validate(state["extraction"])
        specs = analysis.build_requirement_specs(db, db_case_id)
        bids = analysis.build_bids(db, db_case_id, extraction)
    except Exception as exc:
        if state.get("extraction"):
            specs, bids = analysis.build_requirement_specs(db, db_case_id), analysis.build_bids(db, db_case_id)
        else:
            return _fatal(state, "supplier_evaluation", exc)
    return dict(
        requirement_specs=[s.model_dump(mode="json") for s in specs],
        supplier_bids=[b.model_dump(mode="json") for b in bids],
    )


# ---------------------------------------------------------------------------
# G. DETERMINISTIC VERIFICATION NODE   (CRITICAL — reuses the verifier)
# ---------------------------------------------------------------------------

def deterministic_verification(state: AnalysisState, db: Session) -> dict:
    """Invoke the existing deterministic verifier for every supplier bid.

    The engine is the source of truth. This node never reimplements verification.
    """
    try:
        specs = [RequirementSpec.model_validate(s) for s in state["requirement_specs"]]
        bids = [SupplierBid.model_validate(b) for b in state["supplier_bids"]]
        case_date = state["case_metadata"].get("case_date")
        results = analysis.verify_case(db, state["db_case_id"], specs, bids, case_date=case_date)
    except Exception as exc:
        return _fatal(state, "deterministic_verification", exc)

    if not bids:
        return _fatal(state, "deterministic_verification",
                      ValueError("No supplier quotes extracted — cannot verify."))
    return dict(
        verification_results=results,
        supplier_evaluations=results,
    )


# ---------------------------------------------------------------------------
# H. EVIDENCE RETRIEVAL NODE
# ---------------------------------------------------------------------------

def evidence_retrieval(state: AnalysisState, db: Session) -> dict:
    """Index case documents and retrieve case-scoped supporting evidence.

    Supplier-specific queries use supplier_id filtering when available so
    the retriever only returns chunks from the relevant supplier's documents.
    """
    db_case_id = state["db_case_id"]
    try:
        vector_store = VectorStore.from_settings()
        indexed = index_case_documents(db, db_case_id, vector_store)
        retriever = HybridRetriever(vector_store, db)
    except Exception as exc:
        return dict(retrieval_results=[], retrieval_count=0,
                    warnings=[*state.get("warnings", []),
                              {"node": "evidence_retrieval", "message": f"RAG unavailable: {exc}"}])

    # Build supplier_name -> supplier_id mapping for supplier-specific filtering
    supplier_name_to_id: dict[str, str] = {}
    for s in store.list_suppliers(db, db_case_id):
        supplier_name_to_id[s.name.strip().lower()] = s.id

    queries = _retrieval_queries(state)
    results: list[dict] = []
    for query_text, supplier_name in queries:
        supplier_id = supplier_name_to_id.get(supplier_name.strip().lower()) if supplier_name else None
        started = time.monotonic()
        try:
            hits, details = retriever.retrieve(query_text, case_id=db_case_id, top_k=4,
                                              supplier_id=supplier_id, verbose=True)
        except Exception:
            hits, details = [], {}
        latency_ms = int((time.monotonic() - started) * 1000)
        retrieval_meta = {**details, "hybrid_hits": len(hits)}
        retriever.log(db_case_id, state["request_id"], query_text,
                      {"case_id": db_case_id, "supplier_id": supplier_id}, latency_ms, hits,
                      metadata_json=retrieval_meta)
        results.append({
            "query": query_text,
            "hits": [
                {"chunk_id": h.chunk_id, "document_id": h.document_id,
                 "doc_type": h.doc_type, "page": h.page, "score": h.score,
                 "supplier_id": h.supplier_id}
                for h in hits
            ],
        })
    db.commit()
    return dict(retrieval_results=results, retrieval_count=indexed)


def _retrieval_queries(state: AnalysisState) -> list[tuple[str, str | None]]:
    """Return (query, supplier_name_or_None) pairs for retrieval."""
    queries: list[tuple[str, str | None]] = []
    used: set[str] = set()
    for spec in state.get("requirement_specs", []):
        label = spec.get("label") or spec.get("field")
        q = f"{label} {spec.get('value')} {spec.get('unit') or ''} {spec.get('currency') or ''}".strip()
        if q not in used:
            used.add(q)
            queries.append((q, None))
    for ev in state.get("supplier_evaluations", []):
        if ev.get("status") == "PASS":
            q = f"supplier {ev['supplier_name']} quotation material price delivery"
            if q not in used:
                used.add(q)
                queries.append((q, ev.get("supplier_name")))
    return queries[:8]


# ---------------------------------------------------------------------------
# I. CRITIC NODE
# ---------------------------------------------------------------------------

def critic_node(state: AnalysisState, db: Session) -> dict:
    """Validate that the proposed evaluation/recommendation is evidence-backed.

    Detects missing, contradictory, conflicting, or unsupported evidence per
    supplier/field and marks blocking issues. Also computes the structured
    evidence-coverage verdict. Never recomputes verification facts; the decision
    node uses these issues only to abstain, not to choose a different winner.
    """
    try:
        evaluations = state.get("verification_results", [])
        evidence = store.list_evidence(db, state["db_case_id"])
        requirements = store.list_requirements(db, state["db_case_id"])
        name_to_id = {s.name: s.id for s in store.list_suppliers(db, state["db_case_id"])}
        verdict = critic_service.audit(evaluations, requirements, evidence,
                                       supplier_name_to_id=name_to_id)
    except Exception as exc:
        return dict(critic_results=[{"code": "CRITIC_ERROR", "severity": "blocking",
                                     "message": f"{type(exc).__name__}: {exc}"}],
                    critic_blocked=True, critic_status="BLOCK",
                    evidence_coverage=0.0, supported_decisions=[],
                    unsupported_decisions=[], citation_errors=[], citation_checks=[])

    # Non-blocking citation validation on the critic's decided claims: the
    # evidence ids, document types, pages and supplier associations behind each
    # claim are checked so a retrieval hit cannot be confused with a valid citation.
    try:
        cite = citation_validator.validate_critic_claims(
            [*verdict["supported_decisions"], *verdict["unsupported_decisions"]],
            evidence,
            case_id=state["db_case_id"],
        )
        citation_checks = [
            {"field": c.field, "evidence_id": c.evidence_id, "status": c.status,
             "reason": c.reason, **(c.details or {})}
            for c in cite["checks"]
        ]
    except Exception:
        citation_checks = []

    return dict(
        critic_results=verdict["issues"],
        critic_blocked=verdict["blocked"],
        critic_status=verdict["status"],
        evidence_coverage=verdict["evidence_coverage"],
        supported_decisions=verdict["supported_decisions"],
        unsupported_decisions=verdict["unsupported_decisions"],
        citation_errors=verdict["citation_errors"],
        citation_checks=citation_checks,
    )


# ---------------------------------------------------------------------------
# J. DECISION / SYNTHESIS NODE
# ---------------------------------------------------------------------------

def decision_node(state: AnalysisState, db: Session) -> dict:
    """Synthesize the final result.

    Outcome mapping: RECOMMEND | NO_VALID_SUPPLIER | ABSTAIN. Must respect the
    deterministic verification results — the engine decides who passes; this node
    only ranks, picks the winner, and checks the critic. A critic-blocked
    recommendation is downgraded to ABSTAIN (never overridden with a different
    supplier).
    """
    db_case_id = state["db_case_id"]
    extraction = ExtractionResult.model_validate(state["extraction"]) if state.get("extraction") else None
    try:
        recommendation = analysis.build_recommendation(db, db_case_id,
                                                       state["verification_results"], extraction)
    except Exception as exc:
        store.set_case_status(db, db_case_id, "failed")
        db.commit()
        return _fatal(state, "decision_node", exc)

    recommended = recommendation.get("recommended_supplier")
    blocked = _recommendation_blocked(state, db, recommended)

    # surface the *recommendation-level* critic verdict (coverage metrics) in state
    critic_refined = _recommendation_verdict(state, db, recommended)
    if critic_refined is not None:
        return_patch = {
            "critic_status": critic_refined["status"],
            "evidence_coverage": critic_refined["evidence_coverage"],
            "supported_decisions": critic_refined["supported_decisions"],
            "unsupported_decisions": critic_refined["unsupported_decisions"],
            "citation_errors": critic_refined["citation_errors"],
        }
    else:
        return_patch = {}

    if recommended and blocked:
        status = "insufficient"
        decision_status = "ABSTAIN"
        recommendation = dict(recommendation)
        recommendation["status"] = status
        recommendation["recommended_supplier"] = None
        summary = ("Critic blocked this recommendation because the evidence is not fully"
                   " supported. Decision withheld (abstain).")
        recommendation["summary"] = summary
        recommendation["unknowns"] = [
            *recommendation.get("unknowns", []),
            *[i["message"] for i in state["critic_results"] if i["severity"] == "blocking"],
        ]
        db.commit()
    elif not recommended:
        decision_status = "ABSTAIN" if recommendation.get("status") == "insufficient" else "NO_VALID_SUPPLIER"
    else:
        decision_status = "RECOMMEND"

    # persist the recommendation for every completed outcome so GET /results and
    # the product UI can surface it (additive; decision logic is untouched).
    store.save_recommendation(
        db, case_id=db_case_id,
        recommended_supplier=recommendation.get("recommended_supplier"),
        overall_score=recommendation.get("overall_score", 0.0),
        confidence=recommendation.get("confidence", 0.0),
        status=recommendation.get("status", "recommended" if recommendation.get("recommended_supplier") else "no_valid"),
        summary=recommendation.get("summary"),
        reasons=recommendation.get("reasons", []),
        rejections=recommendation.get("rejections", []),
        unknowns=recommendation.get("unknowns", []),
        risks=recommendation.get("risks", []),
        ranked_suppliers=recommendation.get("ranked_suppliers", []),
    )
    store.set_case_status(db, db_case_id, "completed" if not state.get("errors") else "failed")
    db.commit()
    return dict(
        recommendation=recommendation,
        decision_status=decision_status,
        confidence=recommendation.get("confidence", 0.0),
        case_status="completed" if not state.get("errors") else "failed",
        **return_patch,
    )


# ---------------------------------------------------------------------------
# K. MEMORY WRITE NODE
# ---------------------------------------------------------------------------

def memory_write_node(state: AnalysisState, db: Session) -> dict:
    """Persist historical memory after a completed, evidence-backed run."""
    if state.get("errors") or state.get("case_status") != "completed":
        return dict(memory_write_count=0)
    if state.get("critic_blocked"):
        return dict(
            memory_write_count=0,
            warnings=[*state.get("warnings", []),
                      {"node": "memory_write", "message": "memory write skipped because critic blocked the decision"}],
        )
    try:
        from backend.app.services.memory_writer import write_case_memory

        count = write_case_memory(
            db,
            state["db_case_id"],
            state.get("recommendation", {}),
            state.get("verification_results", []),
        )
        db.commit()
        return dict(memory_write_count=count)
    except Exception as exc:
        return dict(
            memory_write_count=0,
            warnings=[*state.get("warnings", []),
                      {"node": "memory_write", "message": f"memory write skipped: {exc}"}],
        )


def _recommendation_verdict(state: AnalysisState, db: Session,
                            recommended: str | None) -> dict | None:
    """Best-effort recommendation-level critic verdict for coverage metrics.

    Only the recommended supplier is re-audited (a competitor's defects must
    never drive the reported coverage of the decision). Returns None when there
    is nothing to recommend or the audit itself fails.
    """
    if not recommended:
        return None
    try:
        evidence = store.list_evidence(db, state["db_case_id"])
        requirements = store.list_requirements(db, state["db_case_id"])
        name_to_id = {s.name: s.id for s in store.list_suppliers(db, state["db_case_id"])}
        return critic_service.audit(
            state["verification_results"], requirements, evidence,
            recommended_supplier=recommended, supplier_name_to_id=name_to_id)
    except Exception:
        return None


def _recommendation_blocked(state: AnalysisState, db: Session, recommended: str | None) -> bool:
    """Re-audit against the actual recommendation. Only the recommended supplier's
    blocking issues can downgrade the decision (never a competitor's)."""
    if not recommended:
        return False
    try:
        evidence = store.list_evidence(db, state["db_case_id"])
        requirements = store.list_requirements(db, state["db_case_id"])
        name_to_id = {s.name: s.id for s in store.list_suppliers(db, state["db_case_id"])}
        verdict = critic_service.audit(
            state["verification_results"], requirements, evidence,
            recommended_supplier=recommended, supplier_name_to_id=name_to_id)
        return verdict["blocked"]
    except Exception:
        return False
