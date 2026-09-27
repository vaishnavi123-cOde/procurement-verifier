"""Case discovery + ingestion from the dataset folder.

Handles the dynamic mapping between an external case id (e.g. ``bench-006``)
and a database ``ProcurementCase`` row. A case directory is discovered under
``{dataset_root}/synthetic/cases/{case_id}/``; if a ``manifest.json`` is present
its title/case_date/budget metadata is loaded generically (never hard-coded).

Document ingestion is idempotent: files already registered for the case are
skipped, so re-analysis reuses the same stored documents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.procurement import CaseDocument, ProcurementCase
from backend.app.repositories import store
from backend.app.services.ingestion import ingest_document


class CaseNotFoundError(Exception):
    """Raised when no directory or DB case can be resolved for a case id."""


def dataset_case_dir(case_id: str) -> Path:
    return Path(settings.dataset_root) / "synthetic" / "cases" / case_id


def resolve_case_dir(case_id: str) -> Path | None:
    """Return the dataset directory for a case id, or None if it does not exist."""
    directory = dataset_case_dir(case_id)
    return directory if directory.is_dir() else None


def load_manifest_metadata(case_dir: Path) -> dict[str, Any]:
    """Read ``manifest.json`` metadata generically (missing fields default)."""
    manifest_file = case_dir / "manifest.json"
    if not manifest_file.is_file():
        return {}
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "title": data.get("title") or case_dir.name,
        "case_date": data.get("case_date"),
        "template": data.get("template"),
        "tags": data.get("tags") or [],
    }


def create_case_from_metadata(session: Session, case_id: str, case_dir: Path) -> ProcurementCase:
    metadata = load_manifest_metadata(case_dir)
    case = store.create_case(
        session,
        name=metadata.get("title") or case_id,
        description=f"Synthetic benchmark case {case_id}",
        budget=None,
        currency="INR",
        metadata_json={
            "bench_case_id": case_id,
            "case_date": metadata.get("case_date") or None,
            "template": metadata.get("template"),
            "tags": metadata.get("tags") or [],
            "dataset": "synthetic-v1",
        },
    )
    session.commit()
    return case


def find_or_create_case(session: Session, case_id: str) -> tuple[ProcurementCase, Path | None]:
    """Resolve a case to a DB row + optional dataset directory.

    1. If a DB case row exists whose id == case_id, use it directly (uploaded
       cases / cases previously created under their DB UUID).
    2. Otherwise look for a dataset directory ``data/synthetic/cases/{id}`` and
       create a new DB case from its manifest metadata.
    3. Otherwise raise CaseNotFoundError.
    """
    existing = store.get_case(session, case_id)
    if existing is not None:
        case_dir = (
            resolve_case_dir(case_id)
            if existing.metadata_json.get("bench_case_id") == case_id
            else None
        )
        return existing, case_dir

    # A dataset case may already exist under a DB-generated UUID; reuse it.
    known = store.get_case_by_bench_id(session, case_id)
    if known is not None:
        return known, resolve_case_dir(case_id)

    case_dir = resolve_case_dir(case_id)
    if case_dir is not None:
        return create_case_from_metadata(session, case_id, case_dir), case_dir

    # Fall back to cases created under their external id stored in metadata.
    for case in store.list_cases(session, limit=5000):
        if case.metadata_json.get("bench_case_id") == case_id:
            return case, resolve_case_dir(case_id)
    raise CaseNotFoundError(f"No case or dataset directory found for '{case_id}'.")


def discover_pdf_files(case_dir: Path) -> list[Path]:
    """Return every PDF in the case directory, sorted for stable ordering."""
    if not case_dir.is_dir():
        return []
    return sorted(p for p in case_dir.glob("*.pdf") if p.is_file())


def ingest_missing_documents(session: Session, db_case_id: str, case_dir: Path) -> int:
    """Ingest every PDF not yet registered for the case. Idempotent."""
    existing = {d.filename for d in store.list_documents(session, db_case_id)}
    ingested = 0
    for pdf in discover_pdf_files(case_dir):
        if pdf.name in existing:
            continue
        ingest_document(session, db_case_id, pdf.name, pdf.read_bytes())
        ingested += 1
    session.commit()
    return ingested


def list_document_refs(session: Session, db_case_id: str) -> list[dict[str, Any]]:
    """Lightweight document references for graph state (no full page text)."""
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "doc_type": d.doc_type,
            "page_count": d.page_count,
            "status": d.status,
        }
        for d in store.list_documents(session, db_case_id)
    ]