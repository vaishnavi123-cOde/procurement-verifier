"""Document indexing: chunking + persistence + vector indexing for a case."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models.evidence import DocumentChunk
from backend.app.repositories import store
from backend.app.services.ingestion import read_page_text, read_tables
from backend.app.services.pdf import chunk_page
from backend.app.rag.vector_store import VectorStore


def index_case_documents(db: Session, case_id: str, vector_store: VectorStore) -> int:
    """Create chunk rows and vector embeddings for every document in a case.

    Returns the number of chunks indexed. Re-indexing is idempotent (existing
    chunks for the case are removed first). supplier_id is populated from the
    Supplier.source_document_id mapping so supplier-level filtering works.
    """
    documents = store.list_documents(db, case_id)
    # reset chunk rows
    for chunk in db.query(DocumentChunk).filter(DocumentChunk.case_id == case_id).all():
        db.delete(chunk)
    db.flush()

    # Build document_id -> supplier_id mapping from Supplier rows
    doc_to_supplier: dict[str, str] = {}
    for supplier in store.list_suppliers(db, case_id):
        if supplier.source_document_id:
            doc_to_supplier[supplier.source_document_id] = supplier.id

    vectors: list[dict] = []
    count = 0
    for doc in documents:
        supplier_id = doc_to_supplier.get(doc.id, "")
        pages = read_page_text(doc)
        for page_text, page_no in zip(pages, range(1, len(pages) + 1)):
            all_text = page_text
            for table in read_tables(doc):
                if table.get("page") == page_no:
                    rows = table.get("table", [])
                    table_text = "\n".join(
                        " | ".join(c or "" for c in row) for row in rows
                    )
                    all_text = f"{all_text}\n\n{table_text}"
            raw_chunks = chunk_page(all_text, page_no, doc_type=doc.doc_type)
            for ch in raw_chunks:
                row = DocumentChunk(
                    document_id=doc.id,
                    case_id=case_id,
                    doc_type=doc.doc_type,
                    page=ch["page"],
                    section=ch["section"],
                    supplier_id=supplier_id or None,
                    content=ch["content"][:8000],
                )
                db.add(row)
                db.flush()
                vectors.append(
                    {
                        "id": row.id,
                        "content": ch["content"],
                        "metadata": {
                            "case_id": case_id,
                            "document_id": doc.id,
                            "doc_type": doc.doc_type,
                            "page": ch["page"],
                            "section": ch["section"] or "",
                            "supplier_id": supplier_id or "",
                        },
                    }
                )
                count += 1
    vector_store.delete_by_case(case_id)
    if vectors:
        vector_store.index_chunks(vectors)
    db.commit()
    return count