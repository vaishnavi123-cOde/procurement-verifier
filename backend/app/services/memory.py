"""Historical procurement memory retrieval.

Memory is context, never authority. This module reads only persisted
``MemoryEntry`` rows and returns sanitized dictionaries with provenance. The
deterministic verifier does not consume these results.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models.memory import MemoryEntry
from backend.app.repositories import store

HISTORICAL_CONTEXT_LABEL = "HISTORICAL_CONTEXT"
CURRENT_EVIDENCE_LABEL = "CURRENT_EVIDENCE"
FINAL_DECISION_LABEL = "FINAL_DECISION"


def supplier_memory_key(value: str | None) -> str:
    """Stable cross-case supplier key based on normalized supplier name."""
    key = " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())
    punctuation = "".join(chr(c) for c in range(33, 127) if not chr(c).isalnum())
    return key.strip(punctuation)


def retrieve_supplier_memory(
    db: Session,
    *,
    supplier_id: str | None = None,
    supplier_name: str | None = None,
    current_case_id: str | None = None,
    days: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Return historical records for one supplier.

    ``supplier_id`` is resolved through the current database supplier row, then
    matched historically by normalized supplier name because supplier UUIDs are
    case-local in this project.
    """
    resolved_name = supplier_name
    resolved_supplier_id = supplier_id
    if supplier_id:
        supplier = store.get_supplier(db, supplier_id)
        if supplier is not None:
            resolved_name = supplier.name
            resolved_supplier_id = supplier.id
    key = supplier_memory_key(resolved_name or supplier_id)
    if not key:
        return _empty_supplier_memory(supplier_id, resolved_name)

    entries = _filter_time(
        store.query_memory(
            db,
            scope="supplier",
            scope_key=key,
            exclude_source_case_id=current_case_id,
            limit=max(limit * 8, 50),
        ),
        days,
    )
    ranked = _rank_entries(entries, current_case_id=current_case_id)[:limit]
    serialized = [_entry_out(row, relevance=_entry_relevance(row)) for row in ranked]
    performance = [e for e in serialized if e["memory_type"] == "supplier_performance"]
    prices = retrieve_historical_prices(
        db,
        supplier_name=resolved_name or key,
        current_case_id=current_case_id,
        limit=max(limit, 20),
    )
    failures = retrieve_failure_patterns(
        db,
        supplier_name=resolved_name or key,
        current_case_id=current_case_id,
        limit=limit,
    )
    store.log_memory_lookup(
        db,
        case_id=current_case_id or "",
        scope="supplier",
        scope_key=key,
        hits=[{"memory_entry_id": e["id"], "source_case_id": e.get("source_case_id")} for e in serialized],
    )
    return {
        "supplier_id": resolved_supplier_id,
        "supplier_name": resolved_name or key,
        "scope_key": key,
        "records": serialized,
        "performance_records": performance,
        "historical_prices": prices,
        "failure_patterns": failures,
        "provenance": [e["provenance"] for e in serialized],
    }


def retrieve_historical_prices(
    db: Session,
    *,
    supplier_id: str | None = None,
    supplier_name: str | None = None,
    material: str | None = None,
    current_case_id: str | None = None,
    days: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return historical price records and aggregate price ranges."""
    if supplier_id and not supplier_name:
        supplier = store.get_supplier(db, supplier_id)
        supplier_name = supplier.name if supplier else None

    key = supplier_memory_key(supplier_name)
    rows = store.query_memory(
        db,
        scope="supplier" if key else None,
        scope_key=key or None,
        memory_type="supplier_price",
        exclude_source_case_id=current_case_id,
        limit=max(limit * 4, 100),
    )
    rows = _filter_time(rows, days)
    material_key = _token_key(material)
    filtered = []
    for row in rows:
        content = row.content or {}
        if material_key and material_key not in _token_key(content.get("material")):
            continue
        if content.get("price") is None:
            continue
        filtered.append(row)
    ranked = _rank_entries(filtered, current_case_id=current_case_id)[:limit]
    records = [_entry_out(row, relevance=_entry_relevance(row)) for row in ranked]
    values = [float(row.content["price"]) for row in ranked if row.content.get("price") is not None]
    currencies = [str(row.content.get("currency") or "") for row in ranked if row.content.get("currency")]
    currency = Counter(currencies).most_common(1)[0][0] if currencies else None
    return {
        "supplier_name": supplier_name,
        "material": material,
        "currency": currency,
        "count": len(values),
        "min_price": min(values) if values else None,
        "max_price": max(values) if values else None,
        "avg_price": round(mean(values), 2) if values else None,
        "records": records,
    }


def retrieve_failure_patterns(
    db: Session,
    *,
    supplier_id: str | None = None,
    supplier_name: str | None = None,
    current_case_id: str | None = None,
    days: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Return recurring supplier compliance failures with provenance."""
    if supplier_id and not supplier_name:
        supplier = store.get_supplier(db, supplier_id)
        supplier_name = supplier.name if supplier else None
    key = supplier_memory_key(supplier_name)
    rows = store.query_memory(
        db,
        scope="supplier" if key else None,
        scope_key=key or None,
        memory_type="supplier_failure_pattern",
        exclude_source_case_id=current_case_id,
        limit=max(limit * 8, 100),
    )
    rows = _filter_time(rows, days)
    ranked = _rank_entries(rows, current_case_id=current_case_id)

    counts: Counter[str] = Counter()
    for row in ranked:
        content = row.content or {}
        field = str(content.get("field") or "unknown")
        status = str(content.get("status") or "unknown")
        counts[f"{field}:{status}"] += 1

    return {
        "supplier_name": supplier_name,
        "patterns": [
            {
                "pattern": key_name,
                "count": count,
                "field": key_name.split(":", 1)[0],
                "status": key_name.split(":", 1)[1] if ":" in key_name else "",
            }
            for key_name, count in counts.most_common(limit)
        ],
        "records": [_entry_out(row, relevance=_entry_relevance(row)) for row in ranked[:limit]],
    }


def retrieve_similar_cases(
    db: Session,
    *,
    case_id: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Return historically similar procurement cases.

    Similarity is intentionally lightweight and deterministic: overlap in case
    category/template, requirement fields, material tokens, and delivery/price
    presence. No external vector index is needed for this memory layer.
    """
    current = _case_signature(db, case_id)
    rows = store.query_memory(
        db,
        scope="case",
        memory_type="procurement_case",
        exclude_source_case_id=case_id,
        limit=max(limit * 10, 100),
    )
    scored: list[tuple[float, list[str], MemoryEntry]] = []
    for row in rows:
        other = _signature_from_memory(row)
        score, reasons = _similarity(current, other)
        if score > 0:
            scored.append((score, reasons, row))
    scored.sort(key=lambda item: (item[0], item[2].created_at), reverse=True)
    entries = []
    for score, reasons, row in scored[:limit]:
        item = _entry_out(row, relevance=round(score, 3))
        item["matching_fields"] = reasons
        entries.append(item)
    store.log_memory_lookup(
        db,
        case_id=case_id,
        scope="case",
        scope_key=case_id,
        hits=[{"memory_entry_id": e["id"], "source_case_id": e.get("source_case_id")} for e in entries],
    )
    return {"case_id": case_id, "items": entries, "total": len(entries)}


def build_historical_context(db: Session, case_id: str, *, limit: int = 5) -> dict[str, Any]:
    """Build the combined historical context payload for a case."""
    suppliers = store.list_suppliers(db, case_id)
    supplier_contexts = [
        retrieve_supplier_memory(
            db,
            supplier_id=supplier.id,
            supplier_name=supplier.name,
            current_case_id=case_id,
            limit=limit,
        )
        for supplier in suppliers
    ]
    material = _primary_material(db, case_id)
    similar = retrieve_similar_cases(db, case_id=case_id, limit=limit)
    prices = _aggregate_price_context(supplier_contexts, material)
    issues = _aggregate_failure_patterns(supplier_contexts)
    previous_cases = _previous_cases(supplier_contexts, similar)
    provenance = _context_provenance(supplier_contexts, similar)
    return {
        "case_id": case_id,
        "context_type": HISTORICAL_CONTEXT_LABEL,
        "current_evidence_label": CURRENT_EVIDENCE_LABEL,
        "final_decision_label": FINAL_DECISION_LABEL,
        "authority": {
            "memory_is_authoritative": False,
            "deterministic_verification_authoritative": True,
            "rule": (
                "Historical memory may inform risk, confidence, and comparison, "
                "but cannot convert FAIL or UNVERIFIED into PASS."
            ),
        },
        "previous_cases": previous_cases[:limit],
        "supplier_historical_performance": supplier_contexts,
        "historical_price_range": prices,
        "repeated_compliance_issues": issues[:limit],
        "similar_cases": similar["items"],
        "provenance": provenance,
    }


def empty_historical_context(case_id: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "context_type": HISTORICAL_CONTEXT_LABEL,
        "current_evidence_label": CURRENT_EVIDENCE_LABEL,
        "final_decision_label": FINAL_DECISION_LABEL,
        "authority": {
            "memory_is_authoritative": False,
            "deterministic_verification_authoritative": True,
            "rule": (
                "Historical memory may inform risk, confidence, and comparison, "
                "but cannot convert FAIL or UNVERIFIED into PASS."
            ),
        },
        "previous_cases": [],
        "supplier_historical_performance": [],
        "historical_price_range": {"count": 0, "min_price": None, "max_price": None, "avg_price": None},
        "repeated_compliance_issues": [],
        "similar_cases": [],
        "provenance": [],
    }


def _empty_supplier_memory(supplier_id: str | None, supplier_name: str | None) -> dict[str, Any]:
    return {
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "scope_key": supplier_memory_key(supplier_name or supplier_id),
        "records": [],
        "performance_records": [],
        "historical_prices": {"count": 0, "records": []},
        "failure_patterns": {"patterns": [], "records": []},
        "provenance": [],
    }


def _entry_out(row: MemoryEntry, *, relevance: float = 0.0) -> dict[str, Any]:
    evidence_refs = []
    if isinstance(row.content, dict):
        evidence_refs = row.content.get("evidence_refs") or []
    return {
        "id": row.id,
        "scope": row.scope,
        "scope_key": row.scope_key,
        "memory_type": row.memory_type,
        "content": row.content or {},
        "source_case_id": row.source_case_id,
        "source_document_id": row.source_document_id,
        "confidence": row.confidence,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "relevance": relevance,
        "provenance": {
            "memory_entry_id": row.id,
            "source_case_id": row.source_case_id,
            "source_document_id": row.source_document_id,
            "evidence_refs": evidence_refs,
            "confidence": row.confidence,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        },
    }


def _rank_entries(rows: list[MemoryEntry], *, current_case_id: str | None = None) -> list[MemoryEntry]:
    return sorted(
        [row for row in rows if not current_case_id or row.source_case_id != current_case_id],
        key=lambda row: (_entry_relevance(row), row.created_at),
        reverse=True,
    )


def _entry_relevance(row: MemoryEntry) -> float:
    type_weight = {
        "supplier_performance": 1.0,
        "procurement_case": 0.95,
        "supplier_failure_pattern": 0.9,
        "supplier_price": 0.8,
        "decision": 0.65,
    }.get(row.memory_type, 0.5)
    return round(type_weight * max(float(row.confidence or 0.0), 0.05), 3)


def _filter_time(rows: list[MemoryEntry], days: int | None) -> list[MemoryEntry]:
    if not days:
        return rows
    cutoff = datetime.utcnow() - timedelta(days=days)
    return [row for row in rows if row.created_at and row.created_at >= cutoff]


def _case_signature(db: Session, case_id: str) -> dict[str, Any]:
    case = store.get_case(db, case_id)
    requirements = store.list_requirements(db, case_id)
    quotes = store.list_quotes(db, case_id)
    fields = {r.field for r in requirements}
    materials = {_token_key(_value_text(r.value)) for r in requirements if r.field == "material"}
    materials.update(_token_key(q.material) for q in quotes if q.material)
    materials.discard("")
    category_parts = []
    if case:
        category_parts.extend(str(t) for t in case.metadata_json.get("tags", []))
        category_parts.append(str(case.metadata_json.get("template") or ""))
        category_parts.append(str(case.name or ""))
    return {
        "fields": fields,
        "materials": materials,
        "category": _token_key(" ".join(category_parts)),
        "has_price": any(r.field == "price" for r in requirements),
        "has_delivery": any(r.field == "delivery_days" for r in requirements),
    }


def _signature_from_memory(row: MemoryEntry) -> dict[str, Any]:
    content = row.content or {}
    return {
        "fields": set(content.get("major_requirements") or []),
        "materials": {_token_key(m) for m in content.get("materials") or [] if m},
        "category": _token_key(content.get("case_type") or content.get("category")),
        "has_price": bool(content.get("price_ranges")),
        "has_delivery": bool(content.get("delivery_requirements")),
    }


def _similarity(current: dict[str, Any], other: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    fields = set(current.get("fields") or set())
    other_fields = set(other.get("fields") or set())
    field_overlap = fields & other_fields
    if field_overlap:
        score += min(len(field_overlap) / max(len(fields), 1), 1.0) * 0.35
        reasons.extend(sorted(field_overlap))
    material_overlap = set(current.get("materials") or set()) & set(other.get("materials") or set())
    if material_overlap:
        score += 0.35
        reasons.extend(f"material:{m}" for m in sorted(material_overlap))
    current_category = set(str(current.get("category") or "").split())
    other_category = set(str(other.get("category") or "").split())
    if current_category & other_category:
        score += 0.15
        reasons.append("case_type")
    if current.get("has_price") and other.get("has_price"):
        score += 0.075
        reasons.append("price")
    if current.get("has_delivery") and other.get("has_delivery"):
        score += 0.075
        reasons.append("delivery")
    return round(score, 3), reasons


def _primary_material(db: Session, case_id: str) -> str | None:
    for req in store.list_requirements(db, case_id):
        if req.field == "material":
            return _value_text(req.value)
    for quote in store.list_quotes(db, case_id):
        if quote.material:
            return quote.material
    return None


def _aggregate_price_context(supplier_contexts: list[dict[str, Any]], material: str | None) -> dict[str, Any]:
    values: list[float] = []
    records: list[dict[str, Any]] = []
    currencies: list[str] = []
    material_key = _token_key(material)
    for supplier_context in supplier_contexts:
        price_context = supplier_context.get("historical_prices") or {}
        for record in price_context.get("records") or []:
            content = record.get("content") or {}
            if material_key and material_key not in _token_key(content.get("material")):
                continue
            price = content.get("price")
            if price is None:
                continue
            values.append(float(price))
            records.append(record)
            if content.get("currency"):
                currencies.append(str(content.get("currency")))
    currency = Counter(currencies).most_common(1)[0][0] if currencies else None
    return {
        "material": material,
        "currency": currency,
        "count": len(values),
        "min_price": min(values) if values else None,
        "max_price": max(values) if values else None,
        "avg_price": round(mean(values), 2) if values else None,
        "records": records[:10],
    }


def _aggregate_failure_patterns(supplier_contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    examples: dict[str, dict[str, Any]] = {}
    for supplier_context in supplier_contexts:
        supplier_name = supplier_context.get("supplier_name")
        patterns = (supplier_context.get("failure_patterns") or {}).get("patterns") or []
        for pattern in patterns:
            key = str(pattern.get("pattern") or "")
            if not key:
                continue
            counts[key] += int(pattern.get("count") or 0)
            examples.setdefault(key, {"supplier_name": supplier_name, **pattern})
    out = []
    for key, count in counts.most_common():
        item = dict(examples.get(key, {}))
        item["pattern"] = key
        item["count"] = count
        out.append(item)
    return out


def _previous_cases(supplier_contexts: list[dict[str, Any]], similar: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for item in similar.get("items") or []:
        source_case_id = item.get("source_case_id")
        if source_case_id and source_case_id not in seen:
            seen.add(source_case_id)
            cases.append({
                "case_id": source_case_id,
                "memory_type": item.get("memory_type"),
                "relevance": item.get("relevance"),
                "summary": item.get("content", {}),
                "provenance": item.get("provenance"),
            })
    for supplier_context in supplier_contexts:
        for record in supplier_context.get("performance_records") or []:
            source_case_id = record.get("source_case_id")
            if source_case_id and source_case_id not in seen:
                seen.add(source_case_id)
                cases.append({
                    "case_id": source_case_id,
                    "memory_type": record.get("memory_type"),
                    "relevance": record.get("relevance"),
                    "summary": record.get("content", {}),
                    "provenance": record.get("provenance"),
                })
    return cases


def _context_provenance(supplier_contexts: list[dict[str, Any]], similar: dict[str, Any]) -> list[dict[str, Any]]:
    provenance: list[dict[str, Any]] = []
    for supplier_context in supplier_contexts:
        provenance.extend(supplier_context.get("provenance") or [])
        provenance.extend(
            record.get("provenance")
            for record in (supplier_context.get("historical_prices") or {}).get("records") or []
            if record.get("provenance")
        )
    provenance.extend(item.get("provenance") for item in similar.get("items") or [] if item.get("provenance"))
    deduped = []
    seen = set()
    for item in provenance:
        key = item.get("memory_entry_id") if isinstance(item, dict) else None
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _value_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("v", value.get("value", "")))
    return str(value or "")


def _token_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())
