"""Unit tests for the LangGraph analysis pipeline (backend.app.graphs).

Covers graph construction, state initialization, and every node function in
isolation with mocked services, so no PDF / DB work is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.app.graphs.state import AnalysisState, initial_state


def _state(**overrides) -> AnalysisState:
    state = initial_state("bench-006", "req-unit")
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Graph construction + state
# ---------------------------------------------------------------------------

class TestGraphStructure:
    def test_expected_nodes_and_edges(self):
        from backend.app.graphs.graph import graph_structure

        structure = graph_structure()
        assert structure["entry_point"] == "case_loader"
        assert structure["nodes"] == [
            "case_loader", "document_discovery", "requirement_analyzer", "extraction",
            "evidence", "memory_retrieval", "supplier_evaluation",
            "deterministic_verification", "evidence_retrieval", "critic", "decision",
            "memory_write",
        ]
        assert structure["edges"][-1] == ["memory_write", "END"]

    def test_build_analysis_graph_compiles(self, db_session):
        from backend.app.graphs.graph import build_analysis_graph

        compiled = build_analysis_graph(db_session)
        assert compiled is not None

    def test_initial_state_keys(self):
        state = initial_state("bench-x", "req-1")
        for key in ("case_id", "db_case_id", "request_id", "documents", "requirements",
                    "extracted_bids", "certificates", "policy_clauses", "requirement_specs",
                    "supplier_bids", "verification_results", "supplier_evaluations",
                    "evidence", "critic_results", "recommendation", "errors", "node_runs"):
            assert key in state


# ---------------------------------------------------------------------------
# A. case_loader
# ---------------------------------------------------------------------------

class TestCaseLoaderNode:
    def test_resolves_dataset_case(self, db_session):
        from backend.app.graphs.nodes import case_loader

        case = MagicMock()
        case.id = "uuid-1"
        case.name = "Case"
        case.description = "d"
        case.budget = 1000.0
        case.currency = "INR"
        case.metadata_json = {}

        with patch("backend.app.graphs.nodes.svc_case_loader.find_or_create_case",
                   return_value=(case, None)) as find:
            update = case_loader(_state(), db_session)
        find.assert_called_once()
        assert update["db_case_id"] == "uuid-1"
        assert update["case_status"] == "analyzing"
        assert update["case_metadata"]["budget"] == 1000.0

    def test_not_found_is_fatal(self, db_session):
        from backend.app.graphs.nodes import case_loader
        from backend.app.services.case_loader import CaseNotFoundError

        with patch("backend.app.graphs.nodes.svc_case_loader.find_or_create_case",
                   side_effect=CaseNotFoundError("no such case")):
            update = case_loader(_state(case_id="nope-999"), db_session)
        assert update["case_status"] == "failed"
        assert any(e["node"] == "case_loader" for e in update["errors"])


# ---------------------------------------------------------------------------
# D. extraction_node
# ---------------------------------------------------------------------------

class TestExtractionNode:
    def test_merges_bids_certs_policy(self, db_session):
        from backend.app.graphs.nodes import extraction_node

        quote_doc, cert_doc = MagicMock(), MagicMock()
        bid = MagicMock()
        bid.supplier_name = "Acme"
        bid.model_dump.return_value = {"supplier_name": "Acme", "conf": 0.9}
        cert = MagicMock()
        cert.model_dump.return_value = {"cert": True}
        clause = MagicMock()
        clause.model_dump.return_value = {"clause": "x"}

        quote_contribution = MagicMock()
        quote_contribution.bids = {"Acme": bid}
        quote_contribution.certifications = []
        quote_contribution.policy_clauses = []

        cert_contribution = MagicMock()
        cert_contribution.bids = {}
        cert_contribution.certifications = [cert]
        cert_contribution.policy_clauses = []

        policy_contribution = MagicMock()
        policy_contribution.bids = {}
        policy_contribution.certifications = []
        policy_contribution.policy_clauses = [clause]

        docs_by_type = {"quote": [quote_doc], "certificate": [cert_doc],
                        "policy": [MagicMock()], "history": []}

        def fake_docs(db, state, doc_type):
            return docs_by_type.get(doc_type, [])

        def fake_analyze(doc):
            if doc is quote_doc:
                return quote_contribution
            if doc is cert_doc:
                return cert_contribution
            return policy_contribution

        with patch("backend.app.graphs.nodes._document_objects", side_effect=fake_docs):
            with patch("backend.app.graphs.nodes.analyze_document", side_effect=fake_analyze) as analyze:
                update = extraction_node(_state(documents=[
                    {"id": "d1", "doc_type": "quote"},
                    {"id": "d2", "doc_type": "certificate"},
                ]), db_session)

        assert analyze.call_count == 3  # quote + certificate + policy
        assert update["extracted_bids"]["Acme"]["supplier_name"] == "Acme"
        assert update["certificates"] == [{"cert": True}]
        assert update["policy_clauses"] == [{"clause": "x"}]


# ---------------------------------------------------------------------------
# E. evidence_node
# ---------------------------------------------------------------------------

class TestEvidenceNode:
    def test_persists_and_collects_evidence(self, db_session):
        from backend.app.graphs.nodes import evidence_node

        evidence_row = MagicMock()
        evidence_row.id, evidence_row.field, evidence_row.value = "e1", "price", {"v": 100.0}
        evidence_row.document_name, evidence_row.doc_type = "quote.pdf", "quote"
        evidence_row.page, evidence_row.confidence = 1, 0.9

        with patch("backend.app.graphs.nodes.extraction_node"):
            pass
        with patch("backend.app.graphs.nodes.ExtractionResult.model_validate",
                   return_value=MagicMock(model_dump=lambda mode: {"x": 1})) as validate:
            with patch("backend.app.graphs.nodes.analysis.persist_extraction",
                       return_value={}) as persist:
                with patch("backend.app.graphs.nodes.analysis.apply_policy", return_value=0):
                    with patch("backend.app.graphs.nodes.store.list_evidence",
                               return_value=[evidence_row]) as list_ev:
                        update = evidence_node(_state(), db_session)

        validate.assert_called_once()
        persist.assert_called_once()
        assert update["evidence"][0]["field"] == "price"
        assert update["extraction"] == {"x": 1}

    def test_failure_is_fatal(self, db_session):
        from backend.app.graphs.nodes import evidence_node

        with patch("backend.app.graphs.nodes.ExtractionResult.model_validate",
                   side_effect=ValueError("bad extraction")):
            update = evidence_node(_state(), db_session)
        assert update["case_status"] == "failed"
        assert any(e["node"] == "evidence_node" for e in update["errors"])


# ---------------------------------------------------------------------------
# F. supplier_evaluation
# ---------------------------------------------------------------------------

class TestSupplierEvaluationNode:
    def test_builds_specs_and_bids(self, db_session):
        from backend.app.graphs.nodes import supplier_evaluation

        spec = MagicMock()
        spec.model_dump.return_value = {"field": "price"}
        bid = MagicMock()
        bid.model_dump.return_value = {"supplier_name": "Acme"}

        with patch("backend.app.graphs.nodes.ExtractionResult.model_validate",
                   return_value=MagicMock()):
            with patch("backend.app.graphs.nodes.analysis.build_requirement_specs",
                       return_value=[spec]) as build_specs:
                with patch("backend.app.graphs.nodes.analysis.build_bids",
                           return_value=[bid]) as build_bids:
                    update = supplier_evaluation(_state(extraction={"reqs": []}), db_session)

        build_specs.assert_called_once()
        build_bids.assert_called_once()
        assert update["requirement_specs"][0]["field"] == "price"
        assert update["supplier_bids"][0]["supplier_name"] == "Acme"


# ---------------------------------------------------------------------------
# G. deterministic_verification
# ---------------------------------------------------------------------------

class TestVerificationNode:
    def test_invokes_verify_case(self, db_session):
        from backend.app.graphs.nodes import deterministic_verification

        results = [{"supplier_name": "Acme", "passed": True, "status": "PASS"}]
        with patch("backend.app.graphs.nodes.analysis.verify_case",
                   return_value=results) as verify:
            update = deterministic_verification(
                _state(requirement_specs=[{"field": "price", "operator": "lte", "value": 10}],
                       supplier_bids=[{"supplier_name": "Acme"}],
                       case_metadata={"case_date": "2025-07-03"}),
                db_session)

        verify.assert_called_once()
        assert update["verification_results"] == results
        assert update["supplier_evaluations"] == results

    def test_no_bids_is_fatal(self, db_session):
        from backend.app.graphs.nodes import deterministic_verification

        update = deterministic_verification(_state(), db_session)
        assert update["case_status"] == "failed"
        assert any(e["node"] == "deterministic_verification" for e in update["errors"])


# ---------------------------------------------------------------------------
# H. evidence_retrieval
# ---------------------------------------------------------------------------

class TestEvidenceRetrievalNode:
    def test_indexes_and_retrieves(self, db_session):
        from backend.app.graphs.nodes import evidence_retrieval

        retriever = MagicMock()
        retriever.retrieve.return_value = (
            [MagicMock(chunk_id="c1", document_id="d1", doc_type="quote",
                       page=1, score=0.9)],
            {"doc_types": [], "vector_hits": 1, "bm25_hits": 0, "hybrid_hits": 1},
        )
        vector_store = MagicMock()
        retrieval_results = [{"supplier_name": "Acme", "status": "PASS"}]

        with patch("backend.app.graphs.nodes.index_case_documents", return_value=3) as index:
            with patch("backend.app.graphs.nodes.VectorStore.from_settings",
                       return_value=vector_store):
                with patch("backend.app.graphs.nodes.HybridRetriever",
                           return_value=retriever):
                    update = evidence_retrieval(
                        _state(requirement_specs=[{"label": "max_price", "field": "price",
                                                   "value": 1000, "unit": None, "currency": "INR"}],
                               supplier_evaluations=retrieval_results),
                        db_session)

        index.assert_called_once()
        retriever.retrieve.assert_called()
        assert update["retrieval_count"] == 3
        assert update["retrieval_results"][0]["hits"][0]["doc_type"] == "quote"

    def test_rag_unavailable_is_non_fatal(self, db_session):
        from backend.app.graphs.nodes import evidence_retrieval

        with patch("backend.app.graphs.nodes.VectorStore.from_settings",
                   side_effect=RuntimeError("no qdrant")):
            update = evidence_retrieval(_state(), db_session)
        assert update["retrieval_results"] == []
        assert "evidence_retrieval" in update["warnings"][0]["node"]


# ---------------------------------------------------------------------------
# I. critic_node
# ---------------------------------------------------------------------------

class TestCriticNode:
    def test_audits_candidates(self, db_session):
        from backend.app.graphs.nodes import critic_node

        verdict = {"issues": ["issue"], "blocked": True, "status": "BLOCK",
                   "evidence_coverage": 0.0, "supported_decisions": [],
                   "unsupported_decisions": [{"supplier": "Acme"}],
                   "citation_errors": []}
        with patch("backend.app.graphs.nodes.store.list_evidence", return_value=[]):
            with patch("backend.app.graphs.nodes.store.list_requirements", return_value=[]):
                with patch("backend.app.graphs.nodes.store.list_suppliers",
                           return_value=[MagicMock(name="Acme", id="s1")]) as list_suppliers:
                    with patch("backend.app.graphs.nodes.critic_service.audit",
                               return_value=verdict) as audit:
                        update = critic_node(_state(verification_results=[]), db_session)

        audit.assert_called_once()
        list_suppliers.assert_called_once()
        assert update["critic_blocked"] is True
        assert update["critic_results"] == ["issue"]
        assert update["critic_status"] == "BLOCK"
        assert update["evidence_coverage"] == 0.0
        assert update["unsupported_decisions"] == [{"supplier": "Acme"}]


# ---------------------------------------------------------------------------
# J. decision_node
# ---------------------------------------------------------------------------

class TestDecisionNode:
    def _recommendation(self, recommended="Acme", status="recommended"):
        return {
            "id": "r1", "case_id": "cb", "recommended_supplier": recommended,
            "overall_score": 95.0, "confidence": 0.9, "status": status,
            "summary": "ok", "reasons": [], "rejections": [],
            "unknowns": [], "risks": [], "ranked_suppliers": [],
        }

    def test_recommend_flow(self, db_session):
        from backend.app.graphs.nodes import decision_node

        rec = self._recommendation()
        with patch("backend.app.graphs.nodes.analysis.build_recommendation",
                   return_value=rec) as build:
            with patch("backend.app.graphs.nodes._recommendation_blocked",
                       return_value=False):
                with patch("backend.app.graphs.nodes.store.set_case_status"):
                    update = decision_node(
                        _state(verification_results=[{"supplier_name": "Acme", "status": "PASS"}],
                               extraction={"reqs": []},
                               critic_results=[]),
                        db_session)

        build.assert_called_once()
        assert update["decision_status"] == "RECOMMEND"
        assert update["recommendation"]["recommended_supplier"] == "Acme"
        assert update["case_status"] == "completed"

    def test_blocked_recommendation_abstains(self, db_session):
        from backend.app.graphs.nodes import decision_node

        rec = self._recommendation()
        with patch("backend.app.graphs.nodes.analysis.build_recommendation",
                   return_value=rec):
            with patch("backend.app.graphs.nodes._recommendation_blocked",
                       return_value=True):
                with patch("backend.app.graphs.nodes.store.save_recommendation"):
                    with patch("backend.app.graphs.nodes.store.set_case_status"):
                        with patch("backend.app.services.memory_writer.write_case_memory"):
                            update = decision_node(
                                _state(verification_results=[{"supplier_name": "Acme", "status": "PASS"}],
                                       extraction={"reqs": []},
                                       critic_results=[{"supplier": "Acme", "severity": "blocking",
                                                        "code": "UNSUPPORTED_CLAIM", "message": "x"}]),
                                db_session)

        assert update["decision_status"] == "ABSTAIN"
        assert update["recommendation"]["status"] == "insufficient"
        assert update["recommendation"]["recommended_supplier"] is None

    def test_no_valid_supplier(self, db_session):
        from backend.app.graphs.nodes import decision_node

        rec = self._recommendation(recommended=None, status="no_valid")
        with patch("backend.app.graphs.nodes.analysis.build_recommendation",
                   return_value=rec):
            with patch("backend.app.graphs.nodes._recommendation_blocked",
                       return_value=False):
                with patch("backend.app.graphs.nodes.store.set_case_status"):
                    update = decision_node(
                        _state(verification_results=[{"supplier_name": "Acme", "status": "FAIL"}],
                               extraction={"reqs": []},
                               critic_results=[]),
                        db_session)

        assert update["decision_status"] == "NO_VALID_SUPPLIER"


# ---------------------------------------------------------------------------
# K. memory_write_node
# ---------------------------------------------------------------------------

class TestMemoryWriteNode:
    def test_persists_memory_after_completed_run(self, db_session):
        from backend.app.graphs.nodes import memory_write_node

        with patch("backend.app.services.memory_writer.write_case_memory",
                   return_value=3) as write_mem:
            update = memory_write_node(
                _state(case_status="completed", recommendation={"status": "recommended"},
                       verification_results=[]),
                db_session)

        write_mem.assert_called_once()
        assert update["memory_write_count"] == 3

    def test_skips_when_run_failed(self, db_session):
        from backend.app.graphs.nodes import memory_write_node

        with patch("backend.app.services.memory_writer.write_case_memory") as write_mem:
            update = memory_write_node(
                _state(case_status="failed", errors=[{"node": "critic", "error": "x"}]),
                db_session)

        write_mem.assert_not_called()
        assert update["memory_write_count"] == 0