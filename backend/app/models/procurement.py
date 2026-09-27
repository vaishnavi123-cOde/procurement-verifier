"""Core procurement entity models (ORM)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.engine import Base
from backend.app.models.base import new_id, utcnow


class ProcurementCase(Base):
    __tablename__ = "procurement_cases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|ready|analyzing|completed|failed
    owner_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    budget: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CaseDocument(Base):
    __tablename__ = "case_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), index=True)  # rfq|quote|spec|policy|certificate|history|other
    mime_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="uploaded")  # uploaded|processed|failed
    error: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_document_id: Mapped[Optional[str]] = mapped_column(String(32))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    field: Mapped[str] = mapped_column(String(64), nullable=False)  # material|quantity|price|delivery_days|certification|...
    operator: Mapped[str] = mapped_column(String(16), default="eq")  # eq|gte|lte|contains|in
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(32))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    tolerance: Mapped[Optional[float]] = mapped_column(Float)
    label: Mapped[Optional[str]] = mapped_column(String(255))
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    evidence_document_id: Mapped[Optional[str]] = mapped_column(String(32))
    page: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Quote(Base):
    """Extracted supplier quotation (one row per supplier per case)."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(ForeignKey("suppliers.id"))
    supplier_name: Mapped[str] = mapped_column(String(255))
    source_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    material: Mapped[Optional[str]] = mapped_column(String(255))
    quantity: Mapped[Optional[float]] = mapped_column(Float)
    quantity_unit: Mapped[Optional[str]] = mapped_column(String(32))
    price: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    delivery_days: Mapped[Optional[int]] = mapped_column(Integer)
    payment_terms: Mapped[Optional[str]] = mapped_column(String(255))
    warranty_months: Mapped[Optional[int]] = mapped_column(Integer)
    bid_validity_days: Mapped[Optional[int]] = mapped_column(Integer)
    certifications: Mapped[list] = mapped_column(JSON, default=list)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="extracted")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SupplierEvaluationRecord(Base):
    __tablename__ = "supplier_evaluations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(32))
    supplier_name: Mapped[str] = mapped_column(String(255))
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="FAIL")  # PASS|FAIL|WARNING|INSUFFICIENT
    checks: Mapped[list] = mapped_column(JSON, default=list)
    rejection_reasons: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    recommended_supplier: Mapped[Optional[str]] = mapped_column(String(255))
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="recommended")  # recommended|insufficient|no_valid
    summary: Mapped[Optional[str]] = mapped_column(Text)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    rejections: Mapped[list] = mapped_column(JSON, default=list)
    unknowns: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    ranked_suppliers: Mapped[list] = mapped_column(JSON, default=list)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)