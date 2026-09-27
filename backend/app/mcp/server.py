"""MCP server exposing the procurement system's existing capabilities as tools.

Transport is kept fully separate from FastAPI: configuration lives in dedicated
``MCP_*`` environment variables (``MCP_TRANSPORT``, ``MCP_HOST``, ``MCP_PORT``)
and the server binds to loopback only by default. It is runnable standalone:

    python -m backend.app.mcp.server          # stdio (default, agent ecosystem)
    MCP_TRANSPORT=sse python -m backend.app.mcp.server   # SSE on 127.0.0.1:8765

Every tool is a thin adapter over an existing service/repository function (see
``backend.app.mcp.tools``). No verification, ranking, retrieval fusion or
analysis logic lives here.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Annotated, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from backend.app.config import settings
from backend.app.database.engine import SessionLocal, init_db
from backend.app.mcp import schemas, tools
from backend.app.mcp.errors import internal_error
from backend.app.observability.logger import emit, summarize

_MCP_TRANSPORTS = ("stdio", "sse", "streamable-http")

_CaseId = Annotated[str, Field(min_length=1, max_length=64, description="A dataset case id (e.g. bench-006) or a stored DB case id.")]
_SupplierId = Annotated[str, Field(min_length=1, max_length=64, description="The stored DB supplier id (32-char hex).")]
_RequirementId = Annotated[str, Field(min_length=1, max_length=64, description="The stored DB requirement id (32-char hex).")]
_Query = Annotated[str, Field(min_length=1, max_length=500, description="Natural-language retrieval query scoped to the case.")]
_DocType = Annotated[Optional[str], Field(default=None, pattern="^(rfq|quote|spec|policy|certificate|history|other)$",
                                          description="Optional document-type filter for retrieval.")]
_TopK = Annotated[int, Field(default=8, ge=1, le=50, description="Maximum number of hits to return (1-50).")]
_Limit = Annotated[int, Field(default=20, ge=1, le=500, description="Maximum number of history entries to return.")]
_SupplierIdOpt = Annotated[Optional[str], Field(default=None, max_length=64,
                                               description="Optional supplier id to scope retrieval to that supplier's documents.")]
_CaseIdOpt = Annotated[Optional[str], Field(default=None, max_length=64,
                                           description="Optional case id to scope history to a single case.")]


_CASE_FIRST_TOOLS = {
    "get_case", "list_case_documents", "search_evidence", "get_requirement",
    "evaluate_supplier", "run_case_analysis", "get_case_decision", "get_audit_report",
}
_SUPPLIER_FIRST_TOOLS = {"get_supplier", "get_supplier_history"}


def _resolve_tool_case_id(db, tool_name: str, args: tuple) -> str | None:
    """Best-effort resolution of the owning case id for a tool invocation."""
    from backend.app.repositories import store as _store
    from backend.app.services.case_loader import find_or_create_case, CaseNotFoundError

    if tool_name in _CASE_FIRST_TOOLS and args:
        try:
            case, _ = find_or_create_case(db, str(args[0]))
            return case.id
        except (CaseNotFoundError, Exception):
            return str(args[0])
    if tool_name in _SUPPLIER_FIRST_TOOLS and args:
        supplier = _store.get_supplier(db, str(args[0]))
        if supplier is not None:
            return supplier.case_id
    return None


def _record_tool_call(db, tool_name: str, args: tuple, status: str,
                      error: str | None, outputs, started: float) -> None:
    from backend.app.repositories import store as _store

    try:
        case_id = _resolve_tool_case_id(db, tool_name, args)
        if case_id is None:
            return
        duration_ms = int((time.monotonic() - started) * 1000)
        _store.add_tool_call(
            db,
            case_id=case_id,
            request_id=uuid.uuid4().hex[:16],
            agent="mcp",
            tool_name=tool_name,
            inputs=summarize({i: a for i, a in enumerate(args)}),
            outputs=summarize(outputs) if outputs is not None else {},
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
        db.commit()
        emit("tool_call", level=20, request_id=uuid.uuid4().hex[:16],
             case_id=case_id, agent="mcp", tool_name=tool_name,
             status=status, duration_ms=duration_ms)
    except Exception:  # noqa: BLE001 - observability must never break MCP tools
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _dispatch(db, tool_name: str, fn, *args):
    """Run a tool body, recording the call and converting unexpected failures into structured errors."""
    started = time.monotonic()
    status = "ok"
    error_msg: str | None = None
    outputs = None
    try:
        outputs = fn(db, *args)
        return outputs
    except ToolError:
        status = "error"
        error_msg = "client_error"
        raise
    except Exception as exc:  # noqa: BLE001 - client should never see internals
        status = "error"
        error_msg = f"{type(exc).__name__}: {exc}"
        raise internal_error(exc) from exc
    finally:
        _record_tool_call(db, tool_name, args, status, error_msg, outputs, started)


def build_server() -> MCPServer:
    """Construct the MCP server with all ten procurement tools registered."""
    server = MCPServer(
        "procurement-verifier",
        title="Procurement Verifier MCP Server",
        description=(
            "Evidence-backed procurement decision and verification system. "
            "Tools expose case data, hybrid document retrieval, supplier history, "
            "deterministic verification, the LangGraph analysis pipeline and the "
            "evidence critic, with strict per-case data isolation."
        ),
        version=settings.app_version,
        instructions=(
            "Use get_case and list_case_documents to orient on a case, search_evidence "
            "for cited facts, get_requirement to read requirements, evaluate_supplier "
            "for deterministic verdicts (after run_case_analysis persists extraction), "
            "and get_case_decision / get_audit_report for the final decision and its "
            "evidence audit. run_case_analysis must be called once per case before "
            "evaluate_supplier can score suppliers."
        ),
    )

    # ------------------------------------------------------------------ 1
    @server.tool(
        name="get_case",
        description=(
            "Return metadata about a procurement case: title, status, budget, created "
            "metadata, a summary of its requirements, and document/supplier counts. "
            "Accepts a dataset case id (bench-XXX) or a stored DB case id."
        ),
        structured_output=True,
    )
    def get_case(case_id: _CaseId) -> schemas.CaseInfo:
        with SessionLocal() as db:
            return _dispatch(db, "get_case", tools.tool_get_case, case_id)

    # ------------------------------------------------------------------ 2
    @server.tool(
        name="list_case_documents",
        description=(
            "List the documents belonging to a case with structured metadata: "
            "document id, filename, document type, owning supplier id when the "
            "document is a supplier quotation, page count and processing/indexing status."
        ),
        structured_output=True,
    )
    def list_case_documents(case_id: _CaseId) -> schemas.DocumentListResult:
        with SessionLocal() as db:
            return _dispatch(db, "list_case_documents", tools.tool_list_case_documents, case_id)

    # ------------------------------------------------------------------ 3
    @server.tool(
        name="search_evidence",
        description=(
            "Search procurement evidence within a specific case using the existing "
            "hybrid retrieval (semantic vector search fused with BM25 via reciprocal "
            "rank fusion). Returns cited document/page/section evidence with retrieval "
            "scores. Results are always scoped to the requested case; pass supplier_id "
            "to restrict hits to that supplier's documents and doc_type to filter by "
            "document type."
        ),
        structured_output=True,
    )
    def search_evidence(
        case_id: _CaseId,
        query: _Query,
        supplier_id: _SupplierIdOpt = None,
        doc_type: _DocType = None,
        top_k: _TopK = 8,
    ) -> schemas.SearchEvidenceResult:
        with SessionLocal() as db:
            return _dispatch(db, "search_evidence", tools.tool_search_evidence,
                             case_id, query, supplier_id, doc_type, top_k)

    # ------------------------------------------------------------------ 4
    @server.tool(
        name="get_supplier",
        description=(
            "Return the stored record for a supplier: name, the case it participates "
            "in, its source quotation document, metadata and its extracted quotation "
            "(material, quantity, price, delivery, certifications)."
        ),
        structured_output=True,
    )
    def get_supplier(supplier_id: _SupplierId) -> schemas.SupplierInfo:
        with SessionLocal() as db:
            return _dispatch(db, "get_supplier", tools.tool_get_supplier, supplier_id)

    # ------------------------------------------------------------------ 5
    @server.tool(
        name="get_supplier_history",
        description=(
            "Return historical procurement information about a supplier: previously "
            "verified price anchors, performance/decision records and awards, all "
            "derived from completed verified analyses (no invented facts). Optionally "
            "scope history to one case with case_id."
        ),
        structured_output=True,
    )
    def get_supplier_history(
        supplier_id: _SupplierId,
        case_id: _CaseIdOpt = None,
        limit: _Limit = 20,
    ) -> schemas.SupplierHistoryResult:
        with SessionLocal() as db:
            return _dispatch(db, "get_supplier_history", tools.tool_get_supplier_history,
                             supplier_id, case_id, limit)

    # ------------------------------------------------------------------ 6
    @server.tool(
        name="get_requirement",
        description=(
            "Return a single requirement of a case: the normalized requirement, "
            "operator, expected value and unit, required/optional status, the source "
            "document, supporting evidence rows and the per-supplier verification "
            "status for that field."
        ),
        structured_output=True,
    )
    def get_requirement(case_id: _CaseId, requirement_id: _RequirementId) -> schemas.RequirementInfo:
        with SessionLocal() as db:
            return _dispatch(db, "get_requirement", tools.tool_get_requirement, case_id, requirement_id)

    # ------------------------------------------------------------------ 7
    @server.tool(
        name="evaluate_supplier",
        description=(
            "Evaluate a single supplier against a case's requirements using the "
            "deterministic verification engine. Returns overall status, score, "
            "requirement-level PASS/FAIL/WARNING/UNVERIFIED checks with expected vs "
            "actual values, plus evidence references for each check. Requires the "
            "case to have been analyzed first (run_case_analysis) so extraction is "
            "persisted; call it again for re-evaluation."
        ),
        structured_output=True,
    )
    def evaluate_supplier(case_id: _CaseId, supplier_id: _SupplierId) -> schemas.SupplierEvaluationResult:
        with SessionLocal() as db:
            return _dispatch(db, "evaluate_supplier", tools.tool_evaluate_supplier, case_id, supplier_id)

    # ------------------------------------------------------------------ 8
    @server.tool(
        name="run_case_analysis",
        description=(
            "Invoke the full LangGraph analysis pipeline over a case (dataset-backed "
            "or stored). Runs document discovery, requirement extraction, bid "
            "extraction, evidence persistence, deterministic verification, hybrid "
            "retrieval, critic audit and the final decision. Returns the status, "
            "recommendation, supplier evaluations with scores, critic verdict, "
            "evidence coverage and the per-node execution breakdown."
        ),
        structured_output=True,
    )
    def run_case_analysis(case_id: _CaseId) -> schemas.AnalysisResultOut:
        with SessionLocal() as db:
            return _dispatch(db, "run_case_analysis", tools.tool_run_case_analysis, case_id)

    # ------------------------------------------------------------------ 9
    @server.tool(
        name="get_case_decision",
        description=(
            "Return the persisted recommendation/decision for a case: recommended "
            "supplier, decision status (RECOMMEND / ABSTAIN / NO_VALID_SUPPLIER), "
            "score, confidence, reasons, ranked suppliers, blocking issues from the "
            "evidence critic, and the abstention reason when one applies."
        ),
        structured_output=True,
    )
    def get_case_decision(case_id: _CaseId) -> schemas.CaseDecision:
        with SessionLocal() as db:
            return _dispatch(db, "get_case_decision", tools.tool_get_case_decision, case_id)

    # ------------------------------------------------------------------ 10
    @server.tool(
        name="get_audit_report",
        description=(
            "Return the evidence/audit trail for a case: critic verdict (PASS / "
            "WARNING / BLOCK), evidence coverage, supported and unsupported claims, "
            "citation errors, blocking issues, audit codes and the recorded per-node "
            "execution timeline with durations."
        ),
        structured_output=True,
    )
    def get_audit_report(case_id: _CaseId) -> schemas.AuditReport:
        with SessionLocal() as db:
            return _dispatch(db, "get_audit_report", tools.tool_get_audit_report, case_id)

    return server


def main() -> None:
    """Standalone entrypoint: ``python -m backend.app.mcp.server``."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in _MCP_TRANSPORTS:
        raise SystemExit(f"MCP_TRANSPORT must be one of {_MCP_TRANSPORTS}; got {transport!r}")
    init_db()
    server = build_server()
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8765"))
        server.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    main()