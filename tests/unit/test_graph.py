import pytest
from unittest.mock import MagicMock, patch

from backend.app.agents.graph import build_graph, run_orchestrated_analysis
from backend.app.agents.state import GraphState

def test_graph_compiles():
    graph = build_graph()
    assert graph is not None

@patch("backend.app.agents.graph.store")
@patch("backend.app.agents.graph.analyze_case_documents")
@patch("backend.app.agents.graph.verify_case")
@patch("backend.app.agents.graph.build_recommendation")
@patch("backend.app.agents.graph.build_bids")
def test_run_orchestrated_analysis(mock_build_bids, mock_recommendation, mock_verify, mock_analyze, mock_store):
    mock_store.get_case.return_value = MagicMock(metadata_json={"case_date": "2024-01-01"})
    mock_store.list_documents.return_value = []
    
    mock_analyze.return_value = MagicMock(
        requirements=[], bids={}, certifications=[], policy_clauses=[]
    )
    bid_mock = MagicMock()
    bid_mock.model_dump.return_value = {"supplier_name": "A"}
    mock_build_bids.return_value = [bid_mock]
    mock_verify.return_value = [{"supplier_name": "A", "passed": True, "score": 100}]
    mock_recommendation.return_value = {"recommended_supplier": "A"}
    
    # We just want to ensure it runs end-to-end without throwing an exception
    db_mock = MagicMock()
    result = run_orchestrated_analysis(db_mock, "bench-test")
    
    assert result["status"] == "completed"
    assert result["case_id"] == "bench-test"
    assert "recommended_supplier" in result["recommendation"]
