"""Strongly typed LangGraph state for the analysis workflow.

State carries identifiers and lightweight references (document ids, evidence
ids), never full raw PDF contents or SQLAlchemy ORM objects.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AnalysisState(TypedDict, total=False):
    # identity
    case_id: str                 # external / benchmark case id, e.g. "bench-006"
    db_case_id: str              # internal DB case UUID
    request_id: str              # per-request trace id
    case_metadata: dict[str, Any]

    # documents (lightweight refs only)
    documents: list[dict[str, Any]]

    # extraction results (serialized dicts)
    requirements: list[dict[str, Any]]
    extracted_bids: dict[str, dict[str, Any]]
    certificates: list[dict[str, Any]]
    policy_clauses: list[dict[str, Any]]
    extraction: dict[str, Any]   # merged ExtractionResult payload

    # verification inputs / outputs
    requirement_specs: list[dict[str, Any]]
    supplier_bids: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    supplier_evaluations: list[dict[str, Any]]

    # evidence / retrieval
    evidence: list[dict[str, Any]]
    retrieval_results: list[dict[str, Any]]
    retrieval_count: int
    historical_context: dict[str, Any]
    memory_write_count: int

    # critic + decision
    critic_results: list[dict[str, Any]]
    critic_blocked: bool
    critic_status: str            # PASS | WARNING | BLOCK
    evidence_coverage: float      # 0..1 over the decided claims
    supported_decisions: list[dict[str, Any]]
    unsupported_decisions: list[dict[str, Any]]
    citation_errors: list[dict[str, Any]]
    citation_checks: list[dict[str, Any]]   # per-citation validation results
    recommendation: dict[str, Any]
    decision_status: str          # RECOMMEND | NO_VALID_SUPPLIER | ABSTAIN
    confidence: float

    # execution tracking
    case_dir: str | None
    case_status: str
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    node_runs: list[dict[str, Any]]
    started_at: float
    semantic_reasoning: str       # "deterministic" | "llm-assisted"
    llm_used: bool


def initial_state(case_id: str, request_id: str, db_case_id: str | None = None) -> AnalysisState:
    return {
        "case_id": case_id,
        "db_case_id": db_case_id or "",
        "request_id": request_id,
        "case_metadata": {},
        "documents": [],
        "requirements": [],
        "extracted_bids": {},
        "certificates": [],
        "policy_clauses": [],
        "extraction": {},
        "requirement_specs": [],
        "supplier_bids": [],
        "verification_results": [],
        "supplier_evaluations": [],
        "evidence": [],
        "retrieval_results": [],
        "retrieval_count": 0,
        "historical_context": {},
        "memory_write_count": 0,
        "critic_results": [],
        "critic_blocked": False,
        "critic_status": "PASS",
        "evidence_coverage": 1.0,
        "supported_decisions": [],
        "unsupported_decisions": [],
        "citation_errors": [],
        "citation_checks": [],
        "recommendation": {},
        "decision_status": "",
        "confidence": 0.0,
        "case_dir": None,
        "case_status": "draft",
        "errors": [],
        "warnings": [],
        "node_runs": [],
        "started_at": 0.0,
        "semantic_reasoning": "deterministic",
        "llm_used": False,
    }
