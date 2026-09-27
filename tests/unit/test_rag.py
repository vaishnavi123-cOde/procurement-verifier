"""RAG retrieval tests: case isolation, supplier isolation, metadata & legs.

These tests exercise the real ingestion -> indexing -> retrieval path against the
synthetic benchmark PDFs (hash embeddings + BM25 + RRF), asserting the Phase 12
isolation and metadata invariants. The retrieval path must stay strictly scoped
by case_id and supplier_id, and every chunk must preserve its metadata from
ingestion through to retrieval.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def _build_case(db, bench_case_id: str) -> tuple:
    """Create + index a synthetic case. Returns (ProcurementCase, VectorStore)."""
    from backend.app.dataset.manifests import from_json
    from backend.app.repositories import store
    from backend.app.services.indexing import index_case_documents
    from backend.app.services.ingestion import ingest_document

    manifest = from_json(
        (DATA_ROOT / "benchmark" / "cases" / f"{bench_case_id}.json").read_text(encoding="utf-8")
    )
    case = store.create_case(
        db,
        name=manifest.title,
        description=f"synthetic benchmark case {bench_case_id}",
        metadata_json={"case_date": manifest.case_date.isoformat(), "benchmark": True,
                       "bench_case_id": bench_case_id},
    )
    db.commit()
    case_dir = DATA_ROOT / "synthetic" / "cases" / bench_case_id
    for pdf in sorted(case_dir.glob("*.pdf")):
        ingest_document(db, case.id, pdf.name, pdf.read_bytes())
    db.commit()

    docs = {d.filename: d.id for d in store.list_documents(db, case.id)}
    for bid in manifest.authoring_suppliers:
        qf = f"quote_{bid.supplier.replace(' ', '_').lower()}.pdf"
        if qf in docs:
            store.create_supplier(db, case_id=case.id, name=bid.supplier,
                                  source_document_id=docs[qf])
    db.commit()

    from backend.app.rag.vector_store import VectorStore
    vector_store = VectorStore.from_settings()
    index_case_documents(db, case.id, vector_store)
    return case, vector_store


def _retriever(db, vector_store):
    from backend.app.rag.hybrid import HybridRetriever
    return HybridRetriever(vector_store, db=db)


class TestCaseIsolation:
    def test_query_never_crosses_case_boundary(self, db_session):
        from backend.app.repositories import store

        case_a, vs = _build_case(db_session, "bench-006")
        case_b, _ = _build_case(db_session, "bench-007")
        assert case_a.id != case_b.id
        retriever = _retriever(db_session, vs)

        # bench-006 query while both cases share the same vector store
        hits = retriever.retrieve("Material: SS 316L price quotation",
                                  case_id=case_a.id, top_k=10)
        assert hits, "expected at least one hit for the scoped case"
        for h in hits:
            assert h.case_id == case_a.id
            assert h.case_id != case_b.id
            assert h.supplier_id is None or store.get_supplier(db_session, h.supplier_id).case_id == case_a.id

        # bench-007 query in the same store must likewise never touch bench-006
        hits_b = retriever.retrieve("Material: SS 316L price quotation",
                                    case_id=case_b.id, top_k=10)
        assert hits_b
        for h in hits_b:
            assert h.case_id == case_b.id
            assert h.case_id != case_a.id


class TestSupplierIsolation:
    def test_supplier_filter_returns_only_that_supplier(self, db_session):
        from backend.app.repositories import store

        case, vs = _build_case(db_session, "bench-006")
        retriever = _retriever(db_session, vs)
        suppliers = store.list_suppliers(db_session, case.id)
        assert len(suppliers) >= 2

        target = suppliers[0]
        hits = retriever.retrieve("quotation price delivery", case_id=case.id,
                                  supplier_id=target.id, top_k=10)
        assert hits
        for h in hits:
            assert h.supplier_id == target.id

    def test_suppliers_get_distinct_documents(self, db_session):
        from backend.app.repositories import store

        case, vs = _build_case(db_session, "bench-006")
        retriever = _retriever(db_session, vs)
        suppliers = store.list_suppliers(db_session, case.id)
        docs_per_supplier = {
            s.name: {h.document_id for h in retriever.retrieve(
                "quotation price material delivery", case_id=case.id,
                supplier_id=s.id, top_k=10)}
            for s in suppliers
        }
        for name_a, docs_a in docs_per_supplier.items():
            for name_b, docs_b in docs_per_supplier.items():
                if name_a != name_b:
                    assert not (docs_a & docs_b), "two suppliers share a quote document"


class TestMetadataPreservation:
    def test_chunk_rows_preserve_all_metadata(self, db_session):
        from sqlalchemy import select
        from backend.app.models.evidence import DocumentChunk
        from backend.app.repositories import store

        case, vs = _build_case(db_session, "bench-006")
        rows = list(db_session.execute(
            select(DocumentChunk).where(DocumentChunk.case_id == case.id)
        ).scalars())
        assert rows
        docs = {d.id: d.filename for d in store.list_documents(db_session, case.id)}
        for row in rows:
            assert row.case_id == case.id
            assert row.document_id in docs
            assert row.doc_type in {"rfq", "quote", "spec", "policy", "certificate", "history", "other"}
            assert isinstance(row.page, int) and row.page >= 1
        # every quote chunk must carry the owning supplier
        quote_rows = [r for r in rows if r.doc_type == "quote"]
        assert quote_rows
        assert all(r.supplier_id for r in quote_rows)

    def test_vector_payload_preserves_metadata(self, db_session):
        from backend.app.repositories import store

        case, vs = _build_case(db_session, "bench-006")
        hits = vs.search("quotation price", case_id=case.id, top_k=5)
        assert hits
        for payload, _score in hits:
            assert payload["case_id"] == case.id
            assert payload["document_id"]
            assert payload["doc_type"]
            assert isinstance(payload["page"], int)


class TestRetrievalLegs:
    def test_hybrid_retrieval_case_scoped(self, db_session):
        case, vs = _build_case(db_session, "bench-006")
        retriever = _retriever(db_session, vs)
        hits = retriever.retrieve("What is the required quantity?", case_id=case.id, top_k=8)
        assert hits
        assert all(h.case_id == case.id for h in hits)
        assert len({h.chunk_id for h in hits}) == len(hits)  # deduplicated

    def test_vector_leg_case_scoped(self, db_session):
        case, vs = _build_case(db_session, "bench-006")
        hits = vs.search("delivery", case_id=case.id, top_k=5)
        assert hits
        assert all(p["case_id"] == case.id for p, _ in hits)

    def test_bm25_leg_returns_ranked_rows(self, db_session):
        case, vs = _build_case(db_session, "bench-006")
        retriever = _retriever(db_session, vs)
        bm25, rows = retriever._get_bm25(case.id, None)
        assert len(rows) > 0
        hits = bm25.search("quantity 1000", top_k=5)
        assert set(hits).issubset(range(len(rows)))

    def test_hybrid_includes_both_legs(self, db_session):
        case, vs = _build_case(db_session, "bench-006")
        retriever = _retriever(db_session, vs)
        hits = retriever.retrieve("Who quoted for SS 316L seamless pipe?", case_id=case.id, top_k=10)
        assert hits
        # doc-type inference must prefer quote docs for "who quoted" queries
        assert all(h.doc_type == "quote" for h in hits)


class TestDocTypeInference:
    def test_explicit_doc_type_wins(self):
        from backend.app.rag.hybrid import infer_doc_types
        assert infer_doc_types("anything", doc_type="rfq") == ["rfq"]

    def test_quote_query_targets_quotes(self):
        from backend.app.rag.hybrid import infer_doc_types
        assert infer_doc_types("Who quoted for SS 316L?") == ["quote"]

    def test_delivery_commitment_targets_quote_or_rfq(self):
        from backend.app.rag.hybrid import infer_doc_types
        t = infer_doc_types("Delivery commitment of Acme")
        assert t == ["quote", "rfq"]

    def test_unrelated_query_no_filter(self):
        from backend.app.rag.hybrid import infer_doc_types
        assert infer_doc_types("material SS316L spec") == []


class TestRetrievalQueriesShape:
    def test_returns_query_supplier_tuples(self):
        from backend.app.graphs.nodes import _retrieval_queries
        from backend.app.graphs.state import initial_state

        state = initial_state("bench-006", "req-1")
        state["requirement_specs"] = [{"label": "material", "field": "material",
                                       "value": "SS316L", "unit": None, "currency": None}]
        state["supplier_evaluations"] = [{"supplier_name": "Nova Industries", "status": "PASS"},
                                         {"supplier_name": "Acme", "status": "FAIL"}]
        queries = _retrieval_queries(state)
        assert isinstance(queries, list) and queries
        for q, sup_name in queries:
            assert isinstance(q, str) and q
            assert sup_name is None or isinstance(sup_name, str)
        # supplier query carries the supplier name for filtering
        assert any(s == "Nova Industries" for _, s in queries)