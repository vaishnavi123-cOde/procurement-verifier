"""Historical procurement memory endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from backend.app.api.security import require_auth
from backend.app.database.engine import get_session
from backend.app.repositories import store
from backend.app.schemas.api import (
    HistoricalContextOut,
    MemoryEntryOut,
    SimilarCasesOut,
    SupplierMemoryOut,
)
from backend.app.services import memory as memory_service

CaseId = Annotated[str, Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$")]
SupplierId = Annotated[str, Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$")]

cases_router = APIRouter(prefix="/api/cases", tags=["memory"],
                         dependencies=[Depends(require_auth)])
suppliers_router = APIRouter(prefix="/api/suppliers", tags=["memory"],
                             dependencies=[Depends(require_auth)])


@cases_router.get("/{case_id}/memory", response_model=dict[str, list[MemoryEntryOut]])
def get_case_memory(case_id: CaseId, db: Session = Depends(get_session)):
    if store.get_case(db, case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    rows = store.query_memory(db, source_case_id=case_id, limit=200)
    items = [memory_service._entry_out(row) for row in rows]  # sanitized service projection
    return {"items": items}


@cases_router.get("/{case_id}/similar", response_model=SimilarCasesOut)
def get_similar_cases(case_id: CaseId, db: Session = Depends(get_session)):
    if store.get_case(db, case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    result = memory_service.retrieve_similar_cases(db, case_id=case_id)
    db.commit()
    return result


@cases_router.get("/{case_id}/historical-context", response_model=HistoricalContextOut)
def get_historical_context(case_id: CaseId, db: Session = Depends(get_session)):
    if store.get_case(db, case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    result = memory_service.build_historical_context(db, case_id)
    db.commit()
    return result


@suppliers_router.get("/{supplier_id}/memory", response_model=SupplierMemoryOut)
def get_supplier_memory(supplier_id: SupplierId, db: Session = Depends(get_session)):
    supplier = store.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    result = memory_service.retrieve_supplier_memory(
        db,
        supplier_id=supplier_id,
        supplier_name=supplier.name,
        current_case_id=supplier.case_id,
    )
    db.commit()
    return result
