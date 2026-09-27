from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from backend.app.api.security import require_auth
from backend.app.config import settings
from backend.app.database.engine import get_session
from backend.app.graphs.graph import graph_structure, run_analysis
from backend.app.observability.logger import emit, new_request_id
from backend.app.repositories import store
from backend.app.schemas.api import (
    CaseCreate, CaseSummaryList, CaseOut, CaseSummaryOut, RecommendationOut, AnalysisResultOut,
)

CaseId = Annotated[str, Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$")]

router = APIRouter(prefix="/api/cases", tags=["cases"], dependencies=[Depends(require_auth)])


@router.post("", response_model=CaseOut, status_code=201)
def create_case(payload: CaseCreate, db: Session = Depends(get_session)):
    case = store.create_case(
        db,
        name=payload.name,
        description=payload.description,
        budget=payload.budget,
        currency=payload.currency,
        metadata_json=payload.metadata_json,
    )
    db.commit()
    db.refresh(case)
    return case


@router.get("", response_model=CaseSummaryList)
def list_cases(db: Session = Depends(get_session)):
    items = store.list_cases(db)
    latest_recs = store.list_latest_recommendations(db)
    summaries = []
    for case in items:
        summary = CaseSummaryOut.model_validate(case, from_attributes=True)
        rec = latest_recs.get(case.id)
        if rec is not None:
            summary.recommendation = RecommendationOut.model_validate(rec, from_attributes=True)
        summaries.append(summary)
    return {"items": summaries, "total": len(summaries)}


@router.get("/{case_id}", response_model=CaseOut)
def get_case(case_id: CaseId, db: Session = Depends(get_session)):
    case = store.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


@router.post("/{case_id}/analyze", response_model=AnalysisResultOut)
def analyze_case(case_id: CaseId, db: Session = Depends(get_session)):
    """Run the LangGraph analysis pipeline over a case.

    ``case_id`` may be any benchmark id (e.g. ``bench-001``) — the case is
    resolved/created and its documents ingested dynamically from the dataset if
    it does not exist yet.
    """
    request_id = new_request_id()
    try:
        result = run_analysis(db, case_id, request_id=request_id)
    except Exception as exc:
        emit("analyze_failed", level=40, request_id=request_id, case_id=case_id,
             error=str(exc))
        if settings.show_error_detail:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
    if result["errors"] and result["status"] == "failed":
        raise HTTPException(status_code=422, detail=result["errors"][:10])
    result["graph"] = graph_structure()
    return result

@router.get("/{case_id}/results")
def get_case_results(case_id: CaseId, db: Session = Depends(get_session)):
    case = store.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    
    requirements = store.list_requirements(db, case_id)
    suppliers = store.list_suppliers(db, case_id)
    evaluations = store.list_evaluations(db, case_id)
    recommendation = store.get_recommendation(db, case_id)
    
    return {
        "case": case,
        "requirements": requirements,
        "suppliers": suppliers,
        "evaluations": evaluations,
        "recommendation": recommendation
    }

@router.get("/{case_id}/evidence")
def get_case_evidence(case_id: CaseId, db: Session = Depends(get_session)):
    case = store.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    evidence = store.list_evidence(db, case_id)
    return {"items": evidence, "total": len(evidence)}

@router.get("/{case_id}/audit")
def get_case_audit(case_id: CaseId, db: Session = Depends(get_session)):
    case = store.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    executions = store.list_agent_executions(db, case_id)
    tool_calls = store.list_tool_calls(db, case_id)
    spans = store.list_spans(db, case_id)

    return {
        "executions": executions,
        "tool_calls": tool_calls,
        "spans": spans
    }