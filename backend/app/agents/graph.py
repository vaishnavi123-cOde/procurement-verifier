import time
import uuid
from typing import Any

from langgraph.graph import StateGraph, END

from backend.app.agents.state import GraphState
from backend.app.repositories import store
from backend.app.services.extraction.analyzer import analyze_case_documents
from backend.app.services.analysis import (
    persist_extraction,
    apply_policy,
    build_requirement_specs,
    build_bids,
    verify_case,
    build_recommendation,
)
from backend.app.services.memory_writer import write_case_memory

def loader_node(state: GraphState) -> GraphState:
    db = state["db"]
    case_id = state["case_id"]
    store.set_case_status(db, case_id, "analyzing")
    db.commit()
    documents = store.list_documents(db, case_id)
    return {"documents": documents}

def extraction_node(state: GraphState) -> GraphState:
    db = state["db"]
    case_id = state["case_id"]
    documents = state["documents"]
    
    extraction = analyze_case_documents(documents)
    persist_extraction(db, case_id, extraction)
    apply_policy(db, case_id)
    
    requirements = build_requirement_specs(db, case_id)
    bids = build_bids(db, case_id, extraction)
    
    return {
        "extraction": extraction,
        "requirements": requirements,
        "bids": bids,
    }

def verifier_node(state: GraphState) -> GraphState:
    db = state["db"]
    case_id = state["case_id"]
    requirements = state.get("requirements", [])
    bids = state.get("bids", [])
    
    case = store.get_case(db, case_id)
    case_date = case.metadata_json.get("case_date") if case else None
    
    evaluations = verify_case(
        db, case_id, requirements, bids, case_date=case_date
    )
    return {"evaluations": evaluations}

def critic_node(state: GraphState) -> GraphState:
    # A true critic would use LLM to validate reasoning.
    # For now, it passes through to not break the deterministic pipeline.
    # In Phase 11, we add the actual LLM logic here.
    return {"critic_results": {"status": "ok", "flags": []}}

def synthesis_node(state: GraphState) -> GraphState:
    db = state["db"]
    case_id = state["case_id"]
    evaluations = state.get("evaluations", [])
    extraction = state.get("extraction")
    
    if not state.get("bids"):
        store.set_case_status(db, case_id, "failed")
        db.commit()
        return {"status": "failed", "errors": ["No supplier quotes extracted."]}
        
    recommendation = build_recommendation(db, case_id, evaluations, extraction)
    write_case_memory(db, case_id, recommendation)
    
    store.set_case_status(db, case_id, "completed")
    db.commit()
    return {"recommendation": recommendation, "status": "completed"}


def build_graph() -> StateGraph:
    workflow = StateGraph(GraphState)
    
    workflow.add_node("loader", loader_node)
    workflow.add_node("extractor", extraction_node)
    workflow.add_node("verifier", verifier_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("synthesis", synthesis_node)
    
    workflow.set_entry_point("loader")
    workflow.add_edge("loader", "extractor")
    workflow.add_edge("extractor", "verifier")
    workflow.add_edge("verifier", "critic")
    workflow.add_edge("critic", "synthesis")
    workflow.add_edge("synthesis", END)
    
    return workflow.compile()


def run_orchestrated_analysis(db: Any, case_id: str, request_id: str | None = None) -> dict:
    start = time.monotonic()
    request_id = request_id or uuid.uuid4().hex
    
    graph = build_graph()
    
    # Check if case exists
    case = store.get_case(db, case_id)
    if case is None:
        raise ValueError(f"Case {case_id} not found.")
        
    initial_state = GraphState(
        case_id=case_id,
        request_id=request_id,
        db=db,
        started_at=start,
    )
    
    final_state = graph.invoke(initial_state)
    
    duration_ms = int((time.monotonic() - start) * 1000)
    
    status = final_state.get("status", "completed")
    
    return {
        "case_id": case_id,
        "request_id": request_id,
        "status": status,
        "duration_ms": duration_ms,
        "requirements": [r.model_dump(mode="json") for r in final_state.get("requirements", [])],
        "bids": [b.model_dump(mode="json") for b in final_state.get("bids", [])],
        "evaluations": final_state.get("evaluations", []),
        "recommendation": final_state.get("recommendation", {}),
        "error": final_state.get("errors", [""])[0] if final_state.get("errors") else None
    }
