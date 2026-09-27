"""Manifest schemas for benchmark cases (dataset interchange format).

A case manifest contains two halves:

* **authoring** — the facts the document generator renders into PDFs
  (requirements, bid facts per supplier, policy, certificates).
* **ground_truth** — the independently authored expected answers the benchmark
  evaluates against.

`ground_truth` is never read by the production pipeline; it lives only here and
in ``data/benchmark/``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


class RequirementDT(BaseModel):
    field: str = Field(..., description="material|quantity|price|delivery_days|certification|required_field|custom")
    operator: str = "eq"          # eq|gte|lte|contains|in
    value: Any
    unit: Optional[str] = None
    currency: Optional[str] = None
    mandatory: bool = True
    tolerance: Optional[float] = None
    label: str = ""
    source_doc: str = "rfq"       # which document carries the requirement


class CertTruth(BaseModel):
    name: str
    issue: Optional[date] = None
    expiry: Optional[date] = None
    quoted: bool = False          # appears in supplier quote document
    certificated: bool = True     # a certificate document exists for this cert


class BidTruth(BaseModel):
    supplier: str
    outcome: str = "PASS"             # PASS | FAIL | UNVERIFIED
    reason_categories: list[str] = Field(default_factory=list)
    material: str
    quantity: float
    quantity_unit: str
    total_price: Optional[float] = None
    currency: str = "INR"
    delivery_days: Optional[int] = None
    delivery_text: Optional[str] = None
    payment_terms: Optional[str] = None
    bid_validity: Optional[int] = None
    warranty_months: Optional[int] = None
    certs: list[CertTruth] = Field(default_factory=list)
    formatting: dict[str, Any] = Field(default_factory=dict)  # rendering variants
    notes: str = ""


class PolicyTruth(BaseModel):
    enabled: bool = False
    approval_threshold: Optional[float] = None    # INR; clause: orders exceeding it
    mandatory_certs: list[str] = Field(default_factory=list)


class GroundTruth(BaseModel):
    expected_recommendation: Optional[str] = None
    expected_status: str = "recommended"          # recommended|insufficient|no_valid
    expected_outcomes: dict[str, dict] = Field(default_factory=dict)
    expected_requirements: list[RequirementDT] = Field(default_factory=list)
    expected_evidence_fields: dict[str, list[str]] = Field(default_factory=dict)
    expected_abstention: bool = False


class RetrievalTask(BaseModel):
    id: str = ""
    query: str
    expected_doc_ids: list[str] = Field(default_factory=list)


class CaseManifest(BaseModel):
    case_id: str
    title: str
    template: str
    variant: int
    seed: str
    material_id: str
    case_date: date
    synthetic: bool = True
    tags: list[str] = Field(default_factory=list)
    documents: dict[str, str] = Field(default_factory=dict)   # doc_type -> rendered filename
    authoring_requirements: list[RequirementDT] = Field(default_factory=list)
    authoring_suppliers: list[BidTruth] = Field(default_factory=list)
    policy: PolicyTruth = Field(default_factory=PolicyTruth)
    ground_truth: GroundTruth = Field(default_factory=GroundTruth)
    retrieval_tasks: list[RetrievalTask] = Field(default_factory=list)
    public_seeds: list[str] = Field(default_factory=list)    # provenance ids used


def to_json(manifest: CaseManifest) -> str:
    return manifest.model_dump_json(indent=2)


def from_json(text: str) -> CaseManifest:
    return CaseManifest.model_validate_json(text)