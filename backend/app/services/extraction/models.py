"""Extraction output models with provenance.

Every extracted field records where it came from (document, page, section,
exact text) so downstream evidence creation is trivial.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.app.services.verifier import RequirementSpec, SupplierBid


class Provenance(BaseModel):
    document_id: str
    document_name: str
    page: Optional[int] = None
    section: Optional[str] = None
    text: str = ""
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    method: str = "heuristic"


class ExtractedRequirement(BaseModel):
    spec: RequirementSpec
    provenance: Provenance
    confidence: float = 0.9


class ExtractedBid(BaseModel):
    supplier_name: str
    bid: SupplierBid
    provenance: list[Provenance] = Field(default_factory=list)
    confidence: float = 0.9


class ExtractedCertification(BaseModel):
    supplier_name: str
    name: str
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    issuer: Optional[str] = None
    certificate_number: Optional[str] = None
    provenance: Provenance
    confidence: float = 0.9


class ExtractedPolicyClause(BaseModel):
    clause: str
    requirement_field: Optional[str] = None
    value: Any = None
    applicability: str = "all"
    provenance: Provenance
    confidence: float = 0.9


class ExtractionResult(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    bids: dict[str, ExtractedBid] = Field(default_factory=dict)
    certifications: list[ExtractedCertification] = Field(default_factory=list)
    policy_clauses: list[ExtractedPolicyClause] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)