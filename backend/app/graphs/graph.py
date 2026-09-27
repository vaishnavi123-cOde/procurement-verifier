"""LangGraph construction and entry point for the analysis pipeline.

Nodes: case_loader -> document_discovery -> requirement_analyzer ->
extraction -> evidence -> supplier_evaluation -> deterministic_verification ->
evidence_retrieval -> critic -> decision.

Every node runs through ``_run_node`` which records an ``AgentExecution`` row
(started/finished) on the existing execution model, so each analysis is fully
auditable. Failures inside a node never crash the graph: errors are captured in
state and the node's partial update is recorded.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from backend.app.observability.logger import emit, new_request_id

from backend.app.graphs import nodes
from backend.app.graphs.nodes import (
    case_loader,
    critic_node,
    decision_node,
    deterministic_verification,
    document_discovery,
    evidence_node,
    evidence_retrieval,
    extraction_node,
    memory_retrieval_node,
    memory_write_node,
    requirement_analyzer,
    supplier_evaluation,
)
from backend.app.graphs.state import AnalysisState, initial_state
from backend.app.repositories import store

_EDGES = [
    ("case_loader", "document_discovery"),
    ("document_discovery", "requirement_analyzer"),
    ("requirement_analyzer", "extraction"),
    ("extraction", "evidence"),
    ("evidence", "memory_retrieval"),
    ("memory_retrieval", "supplier_evaluation"),
    ("supplier_evaluation", "deterministic_verification"),
    ("deterministic_verification", "evidence_retrieval"),
    ("evidence_retrieval", "critic"),
    ("critic", "decision"),
    ("decision", "memory_write"),
    ("memory_write", END),
]

_NODES: list[tuple[str, Callable[[AnalysisState, Session], dict]]] = [
    ("case_loader", case_loader),
    ("document_discovery", document_discovery),
    ("requirement_analyzer", requirement_analyzer),
    ("extraction", extraction_node),
    ("evidence", evidence_node),
    ("memory_retrieval", memory_retrieval_node),
    ("supplier_evaluation", supplier_evaluation),
    ("deterministic_verification", deterministic_verification),
    ("evidence_retrieval", evidence_retrieval),
    ("critic", critic_node),
    ("decision", decision_node),
    ("memory_write", memory_write_node),
]


def _run_node(db: Session, state: AnalysisState, name: str,
              fn: Callable[[AnalysisState, Session], dict]) -> dict:
    """Execute one node with observability + error containment."""
    case_id = state.get("db_case_id") or state.get("case_id", "")
    request_id = state.get("request_id", "")
    run_id = f"g-{name}-{int(time.monotonic() * 1000)}"
    span_id = f"s-{name}-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    started_at = state.get("started_at") or started

    rec = store.add_agent_execution(
        db, case_id=case_id, request_id=request_id, agent=name, run_id=run_id,
        status="running", task={"node": name},
    )
    db.commit()

    error: str | None = None
    error_type = ""
    status = "ok"
    update: dict[str, Any] = {}
    try:
        update = fn(state, db)
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - node failure must not abort graph
        status = "error"
        error_type = type(exc).__name__
        error = f"{error_type}: {exc}"
        update = dict(
            errors=[*state.get("errors", []), {"node": name, "error": error}],
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    store.finish_agent_execution(db, rec.id, status=status, output=update,
                                 error=error, duration_ms=duration_ms)

    try:
        store.add_span(
            db,
            case_id=case_id, request_id=request_id, span_id=span_id,
            parent_span_id=f"run:{request_id}",
            name=name, kind="node", attributes={"node": name, "run_id": run_id},
            status="OK" if status == "ok" else "ERROR",
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001 - span must not break graph
        pass
    db.commit()

    emit("node_run", level=20, request_id=request_id, case_id=case_id,
         run_id=run_id, node=name, duration_ms=duration_ms,
         status=status,
         **({"error": error, "error_type": error_type} if error else {}))

    if "node_runs" not in update:
        update["node_runs"] = _record_run(state, name, started, started_at, status, error, duration_ms)
    return update


def _record_run(state: AnalysisState, name: str, started: float, started_at: float,
                status: str, error: str | None, duration_ms: int) -> list[dict]:
    runs = [*state.get("node_runs", []), {
        "node": name,
        "status": status,
        "started_at_ms": int((started - started_at) * 1000),
        "duration_ms": duration_ms,
        "error": error,
    }]
    return runs


def build_analysis_graph(db: Session) -> Any:
    """Compile the LangGraph workflow bound to a database session."""
    graph = StateGraph(AnalysisState)

    for name, fn in _NODES:
        graph.add_node(name, lambda state, _fn=fn, _n=name: _run_node(db, state, _n, _fn))

    graph.set_entry_point("case_loader")
    for src, dst in _EDGES:
        graph.add_edge(src, dst)
    return graph.compile()


def run_analysis(db: Session, case_id: str, request_id: str | None = None) -> dict:
    """Invoke the full LangGraph pipeline over a case (dataset-backed or DB case id)."""
    request_id = request_id or uuid.uuid4().hex[:16]
    started = time.monotonic()
    initial = initial_state(case_id, request_id)
    graph = build_analysis_graph(db)

    try:
        final = graph.invoke(initial)
    except Exception as exc:
        final = dict(initial)
        final["errors"] = [*final.get("errors", []),
                           {"node": "graph", "error": f"{type(exc).__name__}: {exc}"}]
        final["case_status"] = "failed"

    duration_ms = int((time.monotonic() - started) * 1000)
    final["duration_ms"] = duration_ms
    final["request_id"] = request_id

    status = final.get("case_status") or "draft"
    if not final.get("errors") and status in ("draft", "analyzing"):
        status = "completed"
        final["case_status"] = "completed"

    try:
        db_case_id = final.get("db_case_id") or case_id
        store.add_span(
            db,
            case_id=db_case_id, request_id=request_id, span_id=f"run:{request_id}",
            parent_span_id=None, name="analysis", kind="analysis",
            attributes={"case_id": case_id, "node_count": len(_NODES)},
            status="OK" if status == "completed" else "ERROR",
            duration_ms=duration_ms,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        pass

    emit("analysis_run", level=20, request_id=request_id,
         case_id=final.get("db_case_id") or case_id,
         duration_ms=duration_ms, status=status, node_count=len(_NODES))

    return {
        "case_id": case_id,
        "db_case_id": final.get("db_case_id") or case_id,
        "request_id": request_id,
        "status": status,
        "decision_status": final.get("decision_status", ""),
        "duration_ms": duration_ms,
        "requirements": final.get("requirements", []),
        "supplier_evaluations": final.get("supplier_evaluations", []),
        "critic_results": final.get("critic_results", []),
        "critic_blocked": final.get("critic_blocked", False),
        "critic_status": final.get("critic_status", "PASS"),
        "evidence_coverage": final.get("evidence_coverage", 1.0),
        "supported_decisions": final.get("supported_decisions", []),
        "unsupported_decisions": final.get("unsupported_decisions", []),
        "citation_errors": final.get("citation_errors", []),
        "citation_checks": final.get("citation_checks", []),
        "recommendation": final.get("recommendation", {}),
        "evidence_count": len(final.get("evidence", [])),
        "retrieval_count": final.get("retrieval_count", 0),
        "historical_context": final.get("historical_context", {}),
        "memory_write_count": final.get("memory_write_count", 0),
        "semantic_reasoning": final.get("semantic_reasoning", "deterministic"),
        "node_runs": final.get("node_runs", []),
        "warnings": final.get("warnings", []),
        "errors": final.get("errors", []),
    }


def graph_structure() -> dict:
    """Human-readable description of the LangGraph (nodes, edges, entry point)."""
    edges = []
    for src, dst in _EDGES:
        edges.append([src, dst if dst is not END else "END"])
    return {
        "entry_point": "case_loader",
        "nodes": [name for name, _ in _NODES],
        "edges": edges,
        "end": "END",
    }
