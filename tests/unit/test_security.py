"""Phase 17 security hardening tests.

Covers: filename/path traversal, oversized + non-PDF uploads, SHA256
deduplication, storage isolation, strict parameter validation, cross-case
document access, cross-supplier evidence isolation, safe error responses,
secret redaction, the configurable authentication layer and rate limiting.

These tests exercise real vectors only (no mocked security behaviour).
"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.config import settings

# Path-isolation helper used by ingestion; keep a local copy of the contract.
UPLOAD_ROOT = settings.upload_dir_path.resolve()


def _make_pdf_bytes() -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "Quotation for SS 316L pipe - 1000 units at INR 850")
    c.save()
    return buf.getvalue()


def _make_case(db):
    from backend.app.repositories import store

    case = store.create_case(db, name="Security Case")
    db.commit()
    return case


# ---------------------------------------------------------------------------
# filename / MIME / size validation
# ---------------------------------------------------------------------------

class TestUploadValidation:
    def test_path_traversal_filename_is_sanitized(self):
        from backend.app.services.pdf import safe_filename

        evil = "../../../etc/passwd.pdf"
        assert safe_filename(evil) == "passwd.pdf"
        assert safe_filename("..%2f..%2fetc%2fshadow.pdf") == ".._2f.._2fetc_2fshadow.pdf"

    def test_oversized_upload_rejected(self):
        from backend.app.services.pdf import FileTooLargeError, validate_upload

        content = b"%PDF-1.4\n" + b"0" * (settings.max_upload_size_mb * 1024 * 1024)
        with pytest.raises(FileTooLargeError):
            validate_upload("big.pdf", content)

    def test_non_pdf_magic_bytes_rejected(self):
        from backend.app.services.pdf import UnsupportedFileTypeError, validate_upload

        with pytest.raises(UnsupportedFileTypeError):
            validate_upload("faker.pdf", b"GIF89a\x00\x01\nnot a pdf")

    def test_wrong_extension_rejected(self):
        from backend.app.services.pdf import UnsupportedFileTypeError, validate_upload

        with pytest.raises(UnsupportedFileTypeError):
            validate_upload("evil.sh", _make_pdf_bytes())

    def test_valid_pdf_accepted(self):
        from backend.app.services.pdf import validate_upload

        validate_upload("real.pdf", _make_pdf_bytes())


class TestIngestionSecurity:
    def test_duplicate_sha256_rejected(self, db_session):
        from backend.app.services.ingestion import (
            DocumentIngestionError,
            DuplicateDocumentError,
            ingest_document,
        )

        case = _make_case(db_session)
        pdf = _make_pdf_bytes()
        ingest_document(db_session, case.id, "dup.pdf", pdf)
        db_session.commit()
        with pytest.raises(DuplicateDocumentError):
            ingest_document(db_session, case.id, "dup-again.pdf", pdf)

    def test_duplicate_across_cases_allowed(self, db_session):
        from backend.app.repositories import store
        from backend.app.services.ingestion import ingest_document

        case_a = _make_case(db_session)
        case_b = _make_case(db_session)
        pdf = _make_pdf_bytes()
        ingest_document(db_session, case_a.id, "same.pdf", pdf)
        db_session.commit()
        ingest_document(db_session, case_b.id, "same.pdf", pdf)
        db_session.commit()
        assert len(store.list_documents(db_session, case_a.id)) == 1
        assert len(store.list_documents(db_session, case_b.id)) == 1

    def test_stored_path_stays_inside_upload_root(self, db_session):
        from pathlib import Path

        from backend.app.services.ingestion import ingest_document

        case = _make_case(db_session)
        doc = ingest_document(db_session, case.id, "path_guard.pdf", _make_pdf_bytes())
        db_session.commit()
        resolved = Path(doc.storage_path).resolve()
        assert resolved.is_file()
        assert resolved.parent == (UPLOAD_ROOT / "cases" / case.id).resolve() or \
            UPLOAD_ROOT in resolved.parents
        assert not resolved.name.startswith("..")


# ---------------------------------------------------------------------------
# API: strict validation, cross-case access, safe errors
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


class TestApiValidation:
    def test_malformed_case_id_rejected(self, client):
        resp = client.get("/api/cases/bad%20id%21")
        assert resp.status_code == 422

    def test_malformed_execution_id_rejected(self, client):
        resp = client.get("/api/executions/..%2F..%2Fetc")
        assert resp.status_code in (404, 422)

    def test_invalid_document_type_rejected(self, client):
        from backend.app.database.engine import SessionLocal
        from backend.app.repositories import store

        with SessionLocal() as db:
            case = store.create_case(db, name="Doc Type Check")
            db.commit()
            case_id = case.id
        resp = client.patch(
            f"/api/cases/{case_id}/documents/nothere",
            json={"doc_type": "php_shell"},
        )
        assert resp.status_code == 422

    def test_cross_case_document_denied(self, client):
        from backend.app.database.engine import SessionLocal
        from backend.app.repositories import store
        from backend.app.services.ingestion import ingest_document

        with SessionLocal() as db:
            case_a = store.create_case(db, name="Cross Case A")
            case_b = store.create_case(db, name="Cross Case B")
            db.commit()
            doc = ingest_document(db, case_a.id, "quoted.pdf", _make_pdf_bytes())
            db.commit()
            doc_id, case_a_id, case_b_id = doc.id, case_a.id, case_b.id

        # Case B tries to claim / modify Case A's document
        resp = client.patch(
            f"/api/cases/{case_b_id}/documents/{doc_id}",
            json={"doc_type": "quote"},
        )
        assert resp.status_code == 404
        # Case A still owns it
        ok = client.patch(
            f"/api/cases/{case_a_id}/documents/{doc_id}",
            json={"doc_type": "quote"},
        )
        assert ok.status_code == 200

    def test_unknown_case_returns_404(self, client):
        assert client.get("/api/cases/does-not-exist").status_code == 404

    def test_oversized_upload_api_rejected(self, client):
        from backend.app.database.engine import SessionLocal
        from backend.app.repositories import store

        with SessionLocal() as db:
            case = store.create_case(db, name="Big Upload")
            db.commit()
            case_id = case.id
        big = b"%PDF-1.4\n" + b"x" * (settings.max_upload_size_mb * 1024 * 1024)
        resp = client.post(
            f"/api/cases/{case_id}/documents",
            files={"file": ("big.pdf", big, "application/pdf")},
        )
        assert resp.status_code == 400

    def test_fake_pdf_upload_rejected(self, client):
        from backend.app.database.engine import SessionLocal
        from backend.app.repositories import store

        with SessionLocal() as db:
            case = store.create_case(db, name="Fake Upload")
            db.commit()
            case_id = case.id
        resp = client.post(
            f"/api/cases/{case_id}/documents",
            files={"file": ("evil.pdf", b"#!/bin/sh\nrm -rf /", "application/pdf")},
        )
        assert resp.status_code == 400

    def test_duplicate_upload_returns_409(self, client):
        from backend.app.database.engine import SessionLocal
        from backend.app.repositories import store

        with SessionLocal() as db:
            case = store.create_case(db, name="Dedup API")
            db.commit()
            case_id = case.id
        pdf = _make_pdf_bytes()
        first = client.post(
            f"/api/cases/{case_id}/documents",
            files={"file": ("a.pdf", pdf, "application/pdf")},
        )
        assert first.status_code == 201
        second = client.post(
            f"/api/cases/{case_id}/documents",
            files={"file": ("b.pdf", pdf, "application/pdf")},
        )
        assert second.status_code == 409


class TestCrossSupplierEvidence:
    def test_supplier_filter_never_crosses_supplier(self, db_session):
        """Vector-store supplier filter returns only that supplier's chunks."""
        from backend.app.repositories import store
        from backend.app.rag.vector_store import VectorStore

        case = _make_case(db_session)
        sup_a = store.create_supplier(db_session, case_id=case.id, name="Supplier A")
        sup_b = store.create_supplier(db_session, case_id=case.id, name="Supplier B")
        db_session.commit()

        vector_store = VectorStore.from_settings()
        import uuid

        id_a, id_b = uuid.uuid4().hex, uuid.uuid4().hex
        vector_store.index_chunks([
            {"id": id_a, "content": "price 800 delivery 30 days",
             "metadata": {"case_id": case.id, "doc_type": "quote", "supplier_id": sup_a.id}},
            {"id": id_b, "content": "price 900 delivery 45 days",
             "metadata": {"case_id": case.id, "doc_type": "quote", "supplier_id": sup_b.id}},
        ])
        try:
            hits = vector_store.search(
                "price delivery", case_id=case.id, supplier_id=sup_a.id, top_k=10
            )
            assert hits
            for payload, _score in hits:
                assert payload["supplier_id"] == sup_a.id
            # the other supplier must appear when no filter (data present, not leaking)
            all_hits = vector_store.search("price delivery", case_id=case.id, top_k=10)
            assert any(p["supplier_id"] == sup_b.id for p, _score in all_hits)
        finally:
            vector_store.close()


# ---------------------------------------------------------------------------
# safe errors
# ---------------------------------------------------------------------------

class TestSafeErrors:
    def test_unhandled_exception_leaks_nothing(self):
        from backend.app.main import unhandled_exception_handler

        app = FastAPI()
        app.exception_handler(Exception)(unhandled_exception_handler)

        @app.get("/boom")
        async def boom():
            raise RuntimeError("secret path C:\\app\\secrets\\config.py failed")

        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert "secrets" not in str(body)
        assert "config.py" not in str(body)
        assert body == {"detail": "Internal server error."}


# ---------------------------------------------------------------------------
# secret redaction (integration: structured log output never contains secrets)
# ---------------------------------------------------------------------------

class TestLogRedaction:
    def test_structured_event_redacts_secrets(self):
        import logging

        from backend.app.observability.logger import JsonFormatter

        record = logging.LogRecord(
            name="sec.test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="mcp call", args=(), exc_info=None,
        )
        record.event = "tool_call"
        record.tool_name = "search_evidence"
        record.fields = {
            "query": "price",
            "inputs": {"api_key": "sk-live-123", "authorization": "Bearer abcdef"},
            "document_path": r"C:\app\data\secret.pdf",
        }
        import json as _json

        data = _json.loads(JsonFormatter().format(record))
        assert data["fields"]["inputs"]["api_key"] == "***REDACTED***"
        assert data["fields"]["inputs"]["authorization"] == "***REDACTED***"
        assert "sk-live-123" not in JsonFormatter().format(record)
        assert "Bearer abcdef" not in JsonFormatter().format(record)


# ---------------------------------------------------------------------------
# configurable authentication
# ---------------------------------------------------------------------------

class TestAuth:
    def test_auth_endpoint_disabled_when_off(self, client):
        resp = client.post("/api/auth/token", json={"bootstrap_token": "x"})
        assert resp.status_code == 404

    def test_auth_enforced_when_enabled(self, client):
        from backend.app.api.security import issue_token

        previous = settings.auth_enabled
        settings.auth_enabled = True
        try:
            # Unauthenticated /api calls are rejected; /health stays open.
            assert client.get("/api/cases").status_code == 401
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200

            # Token endpoint mints a working token with the bootstrap secret.
            ok = client.post(
                "/api/auth/token",
                json={"bootstrap_token": settings.auth_bootstrap_token},
            )
            assert ok.status_code == 200
            token = ok.json()["access_token"]
            assert client.get(
                "/api/cases", headers={"Authorization": f"Bearer {token}"}
            ).status_code == 200

            # A forged token is rejected.
            assert client.get(
                "/api/cases", headers={"Authorization": f"Bearer {token}x"}
            ).status_code == 401
            assert client.get(
                "/api/cases", headers={"Authorization": f"Bearer {issue_token()}"}
            ).status_code == 200
        finally:
            settings.auth_enabled = previous

    def test_wrong_bootstrap_token_rejected(self, client):
        previous = settings.auth_enabled
        settings.auth_enabled = True
        try:
            resp = client.post(
                "/api/auth/token", json={"bootstrap_token": "not-the-secret"}
            )
            assert resp.status_code == 403
        finally:
            settings.auth_enabled = previous


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_limiter_returns_429(self):
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        from backend.app.api.security import RateLimitMiddleware

        async def ok(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/", ok)])
        app.add_middleware(RateLimitMiddleware, per_minute=2)

        previous = settings.rate_limit_enabled
        settings.rate_limit_enabled = True
        try:
            with TestClient(app) as c:
                assert c.get("/").status_code == 200
                assert c.get("/").status_code == 200
                assert c.get("/").status_code == 429
        finally:
            settings.rate_limit_enabled = previous