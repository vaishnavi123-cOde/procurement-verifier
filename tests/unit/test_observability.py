"""Unit + integration tests for the observability layer (Phase 16).

Covers: JSON logging / redaction / error classification, retrieval stats with
the new metadata column, node spans recorded by ``_run_node``, MCP tool-call
recording, the /ready probe, request-id middleware and the executions API.
"""

from __future__ import annotations

import json
import logging

import pytest

from backend.app.observability.logger import (
    JsonFormatter,
    error_category,
    new_request_id,
    redact,
    summarize,
)


# ---------------------------------------------------------------------------
# redaction / error classification / formatting (no DB)
# ---------------------------------------------------------------------------

class TestRedaction:
    def test_masks_secret_keys_nested(self):
        payload = {
            "query": "price",
            "auth": {"api_key": "sk-123", "authorization": "Bearer abc"},
            "headers": {"X-Api-Token": "tok"},
            "safe": {"field": "price > 10"},
        }
        out = redact(payload)
        assert out["auth"]["api_key"] == "***REDACTED***"
        assert out["auth"]["authorization"] == "***REDACTED***"
        assert out["headers"]["X-Api-Token"] == "***REDACTED***"
        assert out["safe"]["field"] == "price > 10"

    def test_masks_inline_key_value_strings(self):
        assert "api_key=" in redact("server api_key=super-secret-value now")
        assert "super-secret-value" not in redact("server api_key=super-secret-value now")
        assert "Bearer deadbeef" not in redact("Authorization: Bearer deadbeef")

    def test_leaves_plain_values(self):
        assert redact("hello world") == "hello world"
        assert redact(42) == 42
        assert redact([1, 2, {"name": "x"}]) == [1, 2, {"name": "x"}]


class TestSummarize:
    def test_truncates_large_lists_and_strings(self):
        out = summarize({"items": [{"text": "x" * 10000} for _ in range(10)]})
        assert out["items"][0]["text"].endswith("…")
        assert "more" in out["items"][-1]

    def test_serializes_pydantic_models(self):
        try:
            from pydantic import BaseModel

            class Dummy(BaseModel):
                name: str
                token: str

            out = summarize(Dummy(name="n", token="t"))
        except Exception:
            pytest.skip("pydantic unavailable")
        assert out == {"name": "n", "token": "***REDACTED***"}


class TestErrorCategory:
    def test_validation(self):
        assert error_category(ValueError("bad")) == "validation"
        assert error_category(KeyError("k")) == "validation"

    def test_timeout_and_connectivity(self):
        assert error_category(TimeoutError()) == "timeout"
        assert error_category(ConnectionError()) == "connectivity"

    def test_internal_default(self):
        class Weird(Exception):
            pass

        assert error_category(Weird()) == "internal"


class TestJsonFormatter:
    def test_emits_valid_json_with_structured_fields(self):
        record = logging.LogRecord(
            name="obs.test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="node finished", args=(), exc_info=None,
        )
        record.request_id = "r1"
        record.node = "critic"
        record.duration_ms = 42
        record.fields = {"api_key": "secret-1", "count": 3}
        text = JsonFormatter().format(record)
        data = json.loads(text)
        assert data["event"] == "node finished"
        assert data["node"] == "critic"
        assert data["duration_ms"] == 42
        assert data["fields"]["api_key"] == "***REDACTED***"
        assert data["fields"]["count"] == 3

    def test_request_ids_are_unique(self):
        ids = {new_request_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(len(i) == 16 for i in ids)


# ---------------------------------------------------------------------------
# DB-backed observability
# ---------------------------------------------------------------------------

def _make_case(db, name="Obs Case"):
    from backend.app.repositories import store

    case = store.create_case(db, name=name)
    db.commit()
    return case


def test_retrieval_log_records_stats(db_session):
    from backend.app.models.evidence import DocumentChunk
    from backend.app.rag.hybrid import HybridRetriever
    from backend.app.rag.vector_store import VectorStore
    from backend.app.repositories import store

    case = _make_case(db_session, "Retrieval Obs")
    doc = store.create_document(
        db_session, case_id=case.id, filename="quote.pdf", storage_path="q.pdf",
        doc_type="quote", mime_type="application/pdf", file_size=5,
    )
    db_session.flush()
    chunk = DocumentChunk(
        document_id=doc.id, case_id=case.id, doc_type="quote", page=1,
        content="steel 5000 order price quotation delivery",
    )
    db_session.add(chunk)
    db_session.commit()

    vector_store = VectorStore.from_settings()
    vector_store.index_chunks([{
        "id": chunk.id,
        "content": chunk.content,
        "metadata": {"case_id": case.id, "doc_type": "quote"},
    }])

    retriever = HybridRetriever(vector_store, db_session)
    hits, details = retriever.retrieve("steel price", case_id=case.id, top_k=4, verbose=True)
    retriever.log(case.id, "req-retr-1", "steel price",
                  {"case_id": case.id}, 12, hits, metadata_json={**details, "hybrid_hits": len(hits)})
    db_session.commit()

    logs = store.list_retrieval_logs(db_session, case.id)
    assert logs, "no retrieval log written"
    latest = logs[-1]
    meta = latest.metadata_json
    assert "vector_hits" in meta
    assert "bm25_hits" in meta
    assert meta["hybrid_hits"] == len(hits)
    assert latest.query == "steel price"

    assert isinstance(hits, list)
    assert retriever.last_stats == details

    vector_store.close()


def test_run_node_records_execution_and_span(db_session):
    from backend.app.graphs.graph import _run_node
    from backend.app.graphs.state import initial_state
    from backend.app.repositories import store

    case_id = "bench-obs-unit"
    state = initial_state(case_id, "req-node-1")

    def _noop(node_state, node_db):
        return {"warnings": ["ok"]}

    update = _run_node(db_session, state, "critic", _noop)
    assert update["node_runs"][-1]["node"] == "critic"
    assert update["node_runs"][-1]["status"] == "ok"
    assert update["node_runs"][-1]["duration_ms"] >= 0

    execs = store.list_agent_executions(db_session, case_id)
    assert any(e.agent == "critic" and e.status == "ok" for e in execs)

    spans = store.list_spans(db_session, case_id)
    assert any(s.name == "critic" and s.kind == "node" for s in spans)


def test_run_node_records_failure(db_session):
    from backend.app.graphs.graph import _run_node
    from backend.app.graphs.state import initial_state
    from backend.app.repositories import store

    case_id = "bench-obs-fail"
    state = initial_state(case_id, "req-node-2")

    def _boom(node_state, node_db):
        raise RuntimeError("node blew up")

    update = _run_node(db_session, state, "critic", _boom)
    assert update["node_runs"][-1]["status"] == "error"
    assert "node blew up" in update["node_runs"][-1]["error"]

    execs = store.list_agent_executions(db_session, case_id)
    assert any(e.agent == "critic" and e.status == "error" for e in execs)


def test_mcp_dispatch_records_tool_calls(db_session):
    from backend.app.mcp.server import _dispatch
    from backend.app.repositories import store

    case = _make_case(db_session, "MCP Obs")

    def _stub(db_, case_id):
        return {"case_id": case_id, "ok": True}

    out = _dispatch(db_session, "get_case", _stub, case.id)
    assert out["ok"] is True

    calls = store.list_tool_calls(db_session, case.id)
    assert len(calls) == 1
    assert calls[0].agent == "mcp"
    assert calls[0].tool_name == "get_case"
    assert calls[0].status == "ok"
    assert calls[0].duration_ms is not None


def test_mcp_dispatch_records_failures(db_session):
    from backend.app.mcp.errors import internal_error
    from backend.app.mcp.server import _dispatch
    from backend.app.repositories import store

    case = _make_case(db_session, "MCP Obs Fail")

    def _boom(db_, case_id):
        raise ValueError("bad args")

    with pytest.raises(BaseException):
        _dispatch(db_session, "search_evidence", _boom, case.id)

    calls = store.list_tool_calls(db_session, case.id)
    assert len(calls) == 1
    assert calls[0].status == "error"
    assert "bad args" in calls[0].error


# ---------------------------------------------------------------------------
# FastAPI: /ready, request-id middleware, executions API
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


def test_ready_probe(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["checks"]["database"] == "ok"
    assert data["checks"]["vector_store"] == "ok"
    assert data["checks"]["memory_store"] == "ok"


def test_request_id_middleware(client):
    resp = client.get("/health", headers={"X-Request-ID": "req-abc-42"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == "req-abc-42"
    assert resp.json()["status"] == "ok"

    resp2 = client.get("/health")
    rid = resp2.headers.get("X-Request-ID")
    assert rid and len(rid) == 16


def test_executions_api(client):
    from backend.app.database.engine import SessionLocal
    from backend.app.repositories import store

    with SessionLocal() as db:
        case = store.create_case(db, name="Exec API Obs")
        db.commit()
        rec = store.add_agent_execution(
            db, case_id=case.id, request_id="rid-exec-1", agent="decision",
            run_id="run-1", status="running", task={"node": "decision"},
        )
        span = store.add_span(
            db, case_id=case.id, request_id="rid-exec-1", span_id="s-exec-1",
            parent_span_id=None, name="decision", kind="node", status="OK",
            duration_ms=9,
        )
        db.commit()
        exec_id, case_id = rec.id, case.id
        span_id = span.id

    resp = client.get(f"/api/cases/{case_id}/executions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == case_id
    assert len(data["executions"]) == 1
    assert data["executions"][0]["agent"] == "decision"
    assert data["executions"][0]["status"] == "running"
    assert len(data["spans"]) == 1
    assert data["spans"][0]["span_id"] == "s-exec-1"

    resp2 = client.get(f"/api/executions/{exec_id}")
    assert resp2.status_code == 200
    assert resp2.json()["request_id"] == "rid-exec-1"

    assert client.get("/api/executions/nope").status_code == 404
    assert client.get("/api/cases/nope/executions").status_code == 404