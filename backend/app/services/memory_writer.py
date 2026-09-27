"""Persistent procurement memory writer.

Only structured facts from completed analysis are written. Current evidence and
deterministic verification remain authoritative; this module creates historical
context that future runs may display or use for risk comparison.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.app.repositories import store
from backend.app.services.memory import supplier_memory_key


def write_case_memory(
    db: Session,
    case_id: str,
    recommendation: dict,
    evaluations: list[dict[str, Any]] | None = None,
) -> int:
    """Write idempotent historical memory derived from verified results."""
    store.delete_memory_for_case(db, case_id)

    case = store.get_case(db, case_id)
    requirements = store.list_requirements(db, case_id)
    quotes = store.list_quotes(db, case_id)
    suppliers = store.list_suppliers(db, case_id)
    evidence_rows = store.list_evidence(db, case_id)
    evaluations = evaluations or [_evaluation_from_row(row) for row in store.list_evaluations(db, case_id)]

    supplier_by_name = {supplier.name.strip().lower(): supplier for supplier in suppliers}
    quote_by_name = {quote.supplier_name.strip().lower(): quote for quote in quotes}
    written = 0

    written += _write_case_memory(
        db,
        case_id=case_id,
        case=case,
        requirements=requirements,
        quotes=quotes,
        recommendation=recommendation,
        evaluations=evaluations,
        evidence_rows=evidence_rows,
    )

    for evaluation in evaluations:
        supplier_name = str(evaluation.get("supplier_name") or "").strip()
        if not supplier_name:
            continue
        supplier = supplier_by_name.get(supplier_name.lower())
        quote = quote_by_name.get(supplier_name.lower())
        supplier_evidence = _evidence_refs(evidence_rows, supplier_id=supplier.id if supplier else None)
        if not supplier_evidence:
            supplier_evidence = _evidence_refs(evidence_rows, supplier_name=supplier_name)

        written += _write_supplier_performance(
            db,
            case_id=case_id,
            supplier_id=supplier.id if supplier else evaluation.get("supplier_id"),
            supplier_name=supplier_name,
            evaluation=evaluation,
            quote=quote,
            recommendation=recommendation,
            evidence_refs=supplier_evidence,
        )
        written += _write_supplier_price(
            db,
            case_id=case_id,
            supplier_id=supplier.id if supplier else evaluation.get("supplier_id"),
            supplier_name=supplier_name,
            quote=quote,
            evidence_refs=_evidence_refs(evidence_rows, supplier_id=supplier.id if supplier else None, field="price")
            or _evidence_refs(evidence_rows, supplier_name=supplier_name, field="price"),
        )
        written += _write_failure_patterns(
            db,
            case_id=case_id,
            supplier_id=supplier.id if supplier else evaluation.get("supplier_id"),
            supplier_name=supplier_name,
            evaluation=evaluation,
            evidence_rows=evidence_rows,
        )
    return written


def _write_case_memory(
    db: Session,
    *,
    case_id: str,
    case: Any,
    requirements: list[Any],
    quotes: list[Any],
    recommendation: dict,
    evaluations: list[dict[str, Any]],
    evidence_rows: list[Any],
) -> int:
    selected = recommendation.get("recommended_supplier") if recommendation.get("status") == "recommended" else None
    rejected = [
        {
            "supplier_name": ev.get("supplier_name"),
            "status": ev.get("status"),
            "failure_reasons": ev.get("rejection_reasons") or ev.get("unknowns") or [],
        }
        for ev in evaluations
        if ev.get("supplier_name") != selected
    ]
    price_values = [float(q.price) for q in quotes if q.price is not None]
    materials = sorted({str(q.material) for q in quotes if q.material})
    materials.extend(
        _requirement_value(req)
        for req in requirements
        if req.field == "material" and _requirement_value(req)
    )
    content = {
        "memory_schema": "phase15.case.v1",
        "case_id": case_id,
        "case_type": _case_type(case),
        "category": _case_type(case),
        "major_requirements": sorted({req.field for req in requirements}),
        "materials": sorted(set(materials)),
        "quantities": [
            {"value": _requirement_value(req), "unit": req.unit}
            for req in requirements
            if req.field == "quantity"
        ],
        "price_ranges": {
            "min": min(price_values) if price_values else None,
            "max": max(price_values) if price_values else None,
            "currency": _common_currency(quotes),
        },
        "delivery_requirements": [
            {"operator": req.operator, "value": _requirement_value(req), "unit": req.unit}
            for req in requirements
            if req.field == "delivery_days"
        ],
        "selected_supplier": selected,
        "rejected_suppliers": rejected,
        "failure_reasons": _case_failure_reasons(evaluations),
        "recommendation_outcome": recommendation.get("status"),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "evidence_refs": _evidence_refs(evidence_rows, max_refs=20),
    }
    store.add_memory_entry(
        db,
        scope="case",
        scope_key=case_id,
        memory_type="procurement_case",
        content=content,
        source_case_id=case_id,
        source_document_id=_first_document_id(quotes),
        confidence=float(recommendation.get("confidence") or 0.0),
    )
    return 1


def _write_supplier_performance(
    db: Session,
    *,
    case_id: str,
    supplier_id: str | None,
    supplier_name: str,
    evaluation: dict[str, Any],
    quote: Any,
    recommendation: dict,
    evidence_refs: list[dict[str, Any]],
) -> int:
    selected = recommendation.get("recommended_supplier") == supplier_name
    content = {
        "memory_schema": "phase15.supplier_performance.v1",
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "case_id": case_id,
        "requirement_outcomes": [
            {
                "requirement": check.get("requirement"),
                "field": check.get("field"),
                "status": check.get("status"),
                "expected": check.get("expected"),
                "actual": check.get("actual"),
                "reason": check.get("reason"),
            }
            for check in evaluation.get("checks", [])
        ],
        "final_score": evaluation.get("score"),
        "final_status": evaluation.get("status"),
        "delivery_outcome": _check_status(evaluation, "delivery_days"),
        "certification_outcome": _check_status(evaluation, "certification"),
        "price_information": _quote_price(quote),
        "recommendation_outcome": "selected" if selected else "not_selected",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "rejection_reasons": evaluation.get("rejection_reasons") or [],
        "unknowns": evaluation.get("unknowns") or [],
        "risks": evaluation.get("risks") or [],
        "evidence_refs": evidence_refs,
    }
    store.add_memory_entry(
        db,
        scope="supplier",
        scope_key=supplier_memory_key(supplier_name),
        memory_type="supplier_performance",
        content=content,
        source_case_id=case_id,
        source_document_id=_first_quote_document_id(quote),
        confidence=_confidence(evaluation, recommendation),
    )
    return 1


def _write_supplier_price(
    db: Session,
    *,
    case_id: str,
    supplier_id: str | None,
    supplier_name: str,
    quote: Any,
    evidence_refs: list[dict[str, Any]],
) -> int:
    if quote is None or quote.price is None or not evidence_refs:
        return 0
    content = {
        "memory_schema": "phase15.supplier_price.v1",
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "case_id": case_id,
        "material": quote.material,
        "quantity": quote.quantity,
        "quantity_unit": quote.quantity_unit,
        "currency": quote.currency or "INR",
        "price": quote.price,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "evidence_refs": evidence_refs,
    }
    store.add_memory_entry(
        db,
        scope="supplier",
        scope_key=supplier_memory_key(supplier_name),
        memory_type="supplier_price",
        content=content,
        source_case_id=case_id,
        source_document_id=_first_quote_document_id(quote),
        confidence=float(getattr(quote, "confidence", 0.0) or 0.0),
    )
    return 1


def _write_failure_patterns(
    db: Session,
    *,
    case_id: str,
    supplier_id: str | None,
    supplier_name: str,
    evaluation: dict[str, Any],
    evidence_rows: list[Any],
) -> int:
    count = 0
    for check in evaluation.get("checks", []):
        if check.get("status") not in ("FAIL", "UNVERIFIED"):
            continue
        field = str(check.get("field") or "")
        refs = (
            _evidence_refs(evidence_rows, supplier_id=supplier_id, field=field)
            or _evidence_refs(evidence_rows, supplier_name=supplier_name, field=field)
            or _evidence_refs(evidence_rows, field=field)
            or _evidence_refs(evidence_rows, supplier_id=supplier_id)
            or _evidence_refs(evidence_rows, supplier_name=supplier_name)
            or _evidence_refs(evidence_rows, max_refs=5)
        )
        content = {
            "memory_schema": "phase15.failure_pattern.v1",
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "case_id": case_id,
            "field": field,
            "status": check.get("status"),
            "reason": check.get("reason"),
            "expected": check.get("expected"),
            "actual": check.get("actual"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "evidence_refs": refs,
        }
        store.add_memory_entry(
            db,
            scope="supplier",
            scope_key=supplier_memory_key(supplier_name),
            memory_type="supplier_failure_pattern",
            content=content,
            source_case_id=case_id,
            source_document_id=None,
            confidence=0.9 if check.get("status") == "FAIL" else 0.65,
        )
        count += 1
    return count


def _evaluation_from_row(row: Any) -> dict[str, Any]:
    return {
        "supplier_id": row.supplier_id,
        "supplier_name": row.supplier_name,
        "passed": row.passed,
        "score": row.score,
        "status": row.status,
        "checks": row.checks or [],
        "rejection_reasons": row.rejection_reasons or [],
        "unknowns": [],
        "risks": row.risks or [],
    }


def _evidence_refs(
    evidence_rows: list[Any],
    *,
    supplier_id: str | None = None,
    supplier_name: str | None = None,
    field: str | None = None,
    max_refs: int = 10,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    normalized_supplier = supplier_memory_key(supplier_name)
    for ev in evidence_rows:
        if supplier_id and ev.supplier_id != supplier_id:
            continue
        if normalized_supplier and supplier_memory_key(ev.document_name).find(normalized_supplier) < 0:
            continue
        if field and ev.field != field and not ev.field.startswith(field):
            continue
        refs.append({
            "evidence_id": ev.id,
            "case_id": ev.case_id,
            "document_id": ev.document_id,
            "document_name": ev.document_name,
            "doc_type": ev.doc_type,
            "page": ev.page,
            "field": ev.field,
        })
        if len(refs) >= max_refs:
            break
    return refs


def _requirement_value(req: Any) -> Any:
    value = req.value
    if isinstance(value, dict):
        return value.get("v", value.get("value"))
    return value


def _case_type(case: Any) -> str:
    if case is None:
        return "unknown"
    metadata = case.metadata_json or {}
    if metadata.get("template"):
        return str(metadata["template"])
    tags = metadata.get("tags") or []
    if tags:
        return str(tags[0])
    return str(case.name or "unknown")


def _common_currency(quotes: list[Any]) -> str | None:
    currencies = [q.currency for q in quotes if q.currency]
    if not currencies:
        return None
    return max(set(currencies), key=currencies.count)


def _case_failure_reasons(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = []
    for ev in evaluations:
        if ev.get("status") == "PASS":
            continue
        failures.append({
            "supplier_name": ev.get("supplier_name"),
            "status": ev.get("status"),
            "reasons": ev.get("rejection_reasons") or ev.get("unknowns") or [],
        })
    return failures


def _check_status(evaluation: dict[str, Any], field: str) -> str | None:
    matches = [
        check.get("status")
        for check in evaluation.get("checks", [])
        if str(check.get("field") or "").startswith(field)
    ]
    if not matches:
        return None
    if "FAIL" in matches:
        return "FAIL"
    if "UNVERIFIED" in matches:
        return "UNVERIFIED"
    if "WARNING" in matches:
        return "WARNING"
    return "PASS"


def _quote_price(quote: Any) -> dict[str, Any] | None:
    if quote is None or quote.price is None:
        return None
    return {
        "price": quote.price,
        "currency": quote.currency,
        "material": quote.material,
        "quantity": quote.quantity,
        "quantity_unit": quote.quantity_unit,
    }


def _confidence(evaluation: dict[str, Any], recommendation: dict) -> float:
    if evaluation.get("status") == "PASS":
        return float(recommendation.get("confidence") or 0.9)
    if evaluation.get("status") == "FAIL":
        return 0.9
    return 0.65


def _first_document_id(quotes: list[Any]) -> str | None:
    for quote in quotes:
        doc_id = _first_quote_document_id(quote)
        if doc_id:
            return doc_id
    return None


def _first_quote_document_id(quote: Any) -> str | None:
    if quote is None:
        return None
    source_ids = getattr(quote, "source_document_ids", None) or []
    return source_ids[0] if source_ids else None
