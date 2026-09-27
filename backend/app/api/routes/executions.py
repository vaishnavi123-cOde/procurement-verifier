"""Execution / observability endpoints over the audit trail."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from backend.app.api.security import require_auth
from backend.app.database.engine import get_session
from backend.app.models.execution import AgentExecution
from backend.app.repositories import store
from backend.app.schemas.api import (
    ExecutionListOut,
    ExecutionOut,
    SpanOut,
    ToolCallAuditOut,
)

CaseId = Annotated[str, Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$")]
ExecutionId = Annotated[str, Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$")]

router = APIRouter(prefix="/api", tags=["executions"], dependencies=[Depends(require_auth)])


@router.get("/cases/{case_id}/executions", response_model=ExecutionListOut)
def list_case_executions(case_id: CaseId, db: Session = Depends(get_session)):
    case = store.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    executions = [
        ExecutionOut.model_validate(e, from_attributes=True)
        for e in store.list_agent_executions(db, case_id)
    ]
    tool_calls = [
        ToolCallAuditOut.model_validate(t, from_attributes=True)
        for t in store.list_tool_calls(db, case_id)
    ]
    spans = [
        SpanOut.model_validate(s, from_attributes=True)
        for s in store.list_spans(db, case_id)
    ]
    return ExecutionListOut(
        case_id=case_id, executions=executions, tool_calls=tool_calls, spans=spans
    )


@router.get("/executions/{execution_id}", response_model=ExecutionOut)
def get_execution(execution_id: ExecutionId, db: Session = Depends(get_session)):
    rec = db.get(AgentExecution, execution_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    return ExecutionOut.model_validate(rec, from_attributes=True)