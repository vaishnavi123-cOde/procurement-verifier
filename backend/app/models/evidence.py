"""Evidence-related models.

Every extracted fact (field-level provenance) is stored as an Evidence row.
Each row can be traced back to a specific document, page, section and text span.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.engine import Base
from backend.app.models.base import new_id, utcnow


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("case_documents.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    doc_type: Mapped[str] = mapped_column(String(64), index=True)
    page: Mapped[int] = mapped_column(Integer, default=1)
    section: Mapped[Optional[str]] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    vector_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExtractedField(Base):
    """Raw extracted field with provenance (used before normalization into Quote/Requirement)."""

    __tablename__ = "extracted_fields"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("case_documents.id"), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(32))
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    page: Mapped[Optional[int]] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(255))
    span_start: Mapped[Optional[int]] = mapped_column(Integer)
    span_end: Mapped[Optional[int]] = mapped_column(Integer)
    text: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str] = mapped_column(String(32), default="heuristic")  # heuristic|pdf_table|llm
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("case_documents.id"), index=True)
    document_name: Mapped[str] = mapped_column(String(512))
    supplier_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    doc_type: Mapped[str] = mapped_column(String(64), default="other")
    page: Mapped[Optional[int]] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(255))
    span_start: Mapped[Optional[int]] = mapped_column(Integer)
    span_end: Mapped[Optional[int]] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(32), default="extraction")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Citation(Base):
    """A claim -> evidence link produced by the analyzer/critic."""

    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("procurement_cases.id"), index=True)
    claim: Mapped[str] = mapped_column(Text)
    field: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[str] = mapped_column(String(32), index=True)
    verified: Mapped[bool] = mapped_column(default=True)  # set by critic after citation validation
    verification_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)