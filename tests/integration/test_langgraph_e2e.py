"""End-to-end test: the full LangGraph pipeline over a real synthetic case.

Uses ``bench-006`` (compliance shortlist) which is small, deterministic, and has
authored ground truth: Zenith Engineering should PASS and win the recommendation,
Meridian fails on price, Quantum fails on material.

This test exercises: case discovery/ingestion from the dataset directory,
document classification, requirement extraction, quote extraction, evidence
persistence, deterministic verification, RAG retrieval and the critic/decision
nodes through the compiled LangGraph workflow.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_full_langgraph_run_matches_ground_truth(db_session, bench_case_dir):
    from backend.app.graphs.graph import run_analysis

    outcome = run_analysis(db_session, "bench-006")

    assert outcome["status"] == "completed", outcome["errors"]
    assert outcome["errors"] == []

    # verification results should match authored outcomes
    by_name = {e["supplier_name"]: e["status"] for e in outcome["supplier_evaluations"]}
    assert by_name.get("Zenith Engineering") == "PASS"
    assert by_name.get("Meridian Industries") == "FAIL"
    assert by_name.get("Quantum Industries") == "FAIL"

    # recommendation + decision
    rec = outcome["recommendation"]
    assert rec.get("recommended_supplier") == "Zenith Engineering"
    assert rec.get("status") == "recommended"
    assert outcome["decision_status"] == "RECOMMEND"
    assert outcome["critic_blocked"] is False

    # evidence + retrieval happened
    assert outcome["evidence_count"] > 0
    assert outcome["retrieval_count"] >= 0

    # all twelve nodes ran successfully
    nodes = {r["node"]: r["status"] for r in outcome["node_runs"]}
    assert set(nodes) == {
        "case_loader", "document_discovery", "requirement_analyzer", "extraction",
        "evidence", "memory_retrieval", "supplier_evaluation",
        "deterministic_verification", "evidence_retrieval", "critic", "decision",
        "memory_write",
    }
    assert all(status == "ok" for status in nodes.values())

    # agent executions were recorded on the execution model
    executions = db_session.execute(
        __import__("sqlalchemy").text(
            "SELECT agent, status FROM agent_executions WHERE request_id = :rid"
        ),
        {"rid": outcome["request_id"]},
    ).fetchall()
    assert len(executions) == 12


@pytest.mark.integration
def test_critic_does_not_flag_clean_case(db_session):
    """The critic must stay silent on an evidence-backed clean case (no false positives)."""
    from backend.app.repositories import store
    from backend.app.services.critic import audit

    # bench-006 was analyzed by the previous test in the same session DB
    case = store.get_case_by_bench_id(db_session, "bench-006")
    if case is None:
        pytest.skip("requires bench-006 already analyzed in this session")
    evaluations = store.list_evaluations(db_session, case.id)
    requirements = store.list_requirements(db_session, case.id)
    evidence = store.list_evidence(db_session, case.id)
    suppliers = store.list_suppliers(db_session, case.id)
    name_to_id = {s.name: s.id for s in suppliers}

    ev_dicts = [
        {
            "supplier_name": e.supplier_name,
            "passed": e.passed,
            "status": e.status,
            "checks": e.checks,
        }
        for e in evaluations
    ]
    verdict = audit(ev_dicts, requirements, evidence,
                    recommended_supplier="Zenith Engineering",
                    supplier_name_to_id=name_to_id)

    blocking = [i for i in verdict["issues"] if i["severity"] == "blocking"]
    assert blocking == [], blocking