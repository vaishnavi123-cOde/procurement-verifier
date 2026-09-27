from typing import TypedDict, Any

class GraphState(TypedDict, total=False):
    case_id: str
    request_id: str
    db: Any  # Session object
    
    # Internal node artifacts
    documents: list[Any]
    extraction: Any  # ExtractionResult
    requirements: list[Any]  # RequirementSpec
    bids: list[Any]  # SupplierBid
    evaluations: list[dict]
    critic_results: dict
    recommendation: dict
    
    status: str
    errors: list[str]
    warnings: list[str]
    duration_ms: int
    started_at: float
