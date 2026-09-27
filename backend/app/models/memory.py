"""Persistent procurement memory model.

Memory entries are only written from verified evidence (never from conversation noise).
Scoped so retrieval can be limited by case, supplier or memory type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.engine import Base
from backend.app.models.base import new_id, utcnow


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(32), default="global")  # global|supplier|material|case
    scope_key: Mapped[str] = mapped_column(String(255), index=True)
    memory_type: Mapped[str] = mapped_column(String(64), index=True)  # supplier_price|supplier_performance|decision|recurring_material|certification
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_case_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    source_document_id: Mapped[Optional[str]] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MemoryLookupLog(Base):
    __tablename__ = "memory_lookup_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(String(32))
    scope_key: Mapped[str] = mapped_column(String(255))
    hits: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)