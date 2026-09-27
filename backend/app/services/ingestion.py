"""Document ingestion: validate, persist, extract and store evidence foundation."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.procurement import CaseDocument
from backend.app.repositories import store
from backend.app.services.pdf import (
    PdfError,
    detect_document_type,
    extract_pdf,
    render_table_texts,
    safe_filename,
    sha256_of,
    validate_upload,
)


class DocumentIngestionError(Exception):
    pass


class DuplicateDocumentError(DocumentIngestionError):
    """The exact same file content was already ingested for this case."""


def _save_to_disk(case_id: str, raw_filename: str, content: bytes) -> tuple[str, str]:
    root = settings.upload_dir_path.resolve()
    case_dir = root / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(raw_filename)
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    path = case_dir / name
    counter = 1
    while path.exists():
        path = case_dir / f"{path.stem}_{counter}{path.suffix}"
        counter += 1
    # Storage isolation guard: never write outside the configured upload root,
    # even if a case_id segment were ever tricked into path traversal.
    resolved = path.resolve()
    if not str(resolved).startswith(str(root)) and root not in resolved.parents:
        raise DocumentIngestionError("Invalid storage path.")
    resolved.write_bytes(content)
    return name, str(resolved)


def ingest_document(session: Session, case_id: str, filename: str,
                    content: bytes, doc_type: Optional[str] = None) -> CaseDocument:
    """Validate, save and index a single uploaded PDF."""
    try:
        validate_upload(filename, content)
    except PdfError as exc:
        raise DocumentIngestionError(str(exc)) from exc

    # SHA256 deduplication: reject re-uploads of identical content for the same
    # case (same document available elsewhere in storage is allowed).
    content_hash = sha256_of(content)
    existing = store.list_documents(session, case_id)
    if any(doc.sha256 == content_hash for doc in existing):
        raise DuplicateDocumentError(
            "This file was already uploaded to this case (identical SHA256 hash)."
        )

    try:
        extraction = extract_pdf(filename, content)
    except PdfError as exc:
        raise DocumentIngestionError(str(exc)) from exc

    if extraction.total_chars() == 0 and not extraction.tables:
        raise DocumentIngestionError(
            "Could not extract any text from this PDF. It may be a scanned image document."
        )

    raw_name, storage_path = _save_to_disk(case_id, filename, content)
    detected = doc_type or detect_document_type(raw_name, " ".join(extraction.pages))
    page_texts = render_table_texts(extraction.pages, extraction.tables)

    document = store.create_document(
        session,
        case_id=case_id,
        filename=raw_name,
        storage_path=storage_path,
        doc_type=detected,
        mime_type="application/pdf",
        file_size=len(content),
        sha256=content_hash,
        page_count=extraction.page_count,
    )
    document.metadata_json = {
        "pages": page_texts,
        "tables": [
            {"page": t.page, "table": t.table}
            for t in extraction.tables
        ],
        "extraction_warnings": extraction.warnings,
    }
    document.status = "processed"
    session.flush()
    return document


def read_file_to_bytes(file: BinaryIO) -> bytes:
    return file.read()


def read_page_text(document: CaseDocument) -> list[str]:
    return document.metadata_json.get("pages") or document.metadata_json.get("page_texts") or []


def read_tables(document: CaseDocument) -> list[dict]:
    return document.metadata_json.get("tables") or []


def open_storage_path(document: CaseDocument) -> Path:
    return Path(document.storage_path)