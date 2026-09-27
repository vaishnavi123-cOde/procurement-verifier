"""Document upload endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.app.api.security import require_auth
from backend.app.database.engine import get_session
from backend.app.repositories import store
from backend.app.schemas.api import DocumentList, DocumentOut, DocumentTypeUpdate
from backend.app.services.ingestion import (
    DocumentIngestionError,
    DuplicateDocumentError,
    ingest_document,
    read_file_to_bytes,
)

router = APIRouter(prefix="/api/cases", tags=["documents"],
                   dependencies=[Depends(require_auth)])

CaseId = Annotated[str, Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$")]
DocId = Annotated[str, Path(..., pattern=r"^[A-Za-z0-9_-]{1,128}$")]


@router.post("/{case_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    case_id: CaseId,
    file: UploadFile = File(...),
    doc_type: str = Form(default="", max_length=64),
    db: Session = Depends(get_session),
):
    case = store.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found.")

    content = await file.read()
    try:
        document = ingest_document(
            db,
            case_id=case_id,
            filename=file.filename or "document.pdf",
            content=content,
            doc_type=doc_type or None,
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DocumentIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(document)
    return document


@router.get("/{case_id}/documents", response_model=DocumentList)
def list_documents(case_id: CaseId, db: Session = Depends(get_session)):
    if store.get_case(db, case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    items = store.list_documents(db, case_id)
    return {"items": items, "total": len(items)}


@router.patch("/{case_id}/documents/{document_id}", response_model=DocumentOut)
def update_document_type(case_id: CaseId, document_id: DocId, payload: DocumentTypeUpdate,
                         db: Session = Depends(get_session)):
    document = store.get_document(db, document_id)
    if document is None or document.case_id != case_id:
        raise HTTPException(status_code=404, detail="Document not found.")
    store.update_document_type(db, document_id, payload.doc_type)
    db.commit()
    db.refresh(document)
    return document