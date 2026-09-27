"""Core analysis pipeline (deterministic core services used by the agents).

This module implements the pure analysis logic: persist extracted facts as
evidence, build requirement spec, verify bids, run policy integration, produce
rankings and the final report. The agent layer (LangGraph) orchestrates these
steps for auditability + optional LLM enhancement; the decision itself never
depends on an LLM.
"""

from __future__ import annotations

import time
import uuid
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.procurement import CaseDocument, ProcurementCase
from backend.app.repositories import store
from backend.app.services.extraction.analyzer import analyze_case_documents
from backend.app.services.extraction.models import ExtractedBid, ExtractedCertification, ExtractionResult
from backend.app.services.verifier import (
    CertificationInfo,
    RequirementSpec,
    SupplierBid,
    VerifierInput,
    evaluate_supplier,
    normalize_currency,
)


# ---------------------------------------------------------------------------
# Persistence of extracted evidence
# ---------------------------------------------------------------------------

def persist_extraction(db: Session, case_id: str, extraction: ExtractionResult) -> dict:
    """Write extracted fields/requirements/evidence to the database.

    Returns stats + a mapping supplier_name -> {field: [evidence-id, ...]} and
    supplier_id map for the pipeline.
    """
    supplier_ids: dict[str, str] = {}
    bid_evidence: dict[str, dict[str, list[str]]] = {}
    docs_by_id = {d.id: d for d in store.list_documents(db, case_id)}

    def doc_label(doc_id: str) -> str:
        d = docs_by_id.get(doc_id)
        return d.filename if d else doc_id

    # Requirements + evidence ------------------------------------------------
    for req in extraction.requirements:
        prov = req.provenance
        ev = store.add_evidence(
            db,
            case_id=case_id,
            document_id=prov.document_id,
            document_name=doc_label(prov.document_id),
            field=req.spec.field,
            value=req.spec.value,
            text=prov.text,
            page=prov.page,
            section=prov.section,
            confidence=req.confidence,
            doc_type=docs_by_id.get(prov.document_id).doc_type if prov.document_id in docs_by_id else "other",
        )
        store.add_requirement(
            db,
            case_id=case_id,
            field=req.spec.field,
            operator=req.spec.operator.value,
            value=req.spec.value,
            unit=req.spec.unit,
            currency=req.spec.currency,
            mandatory=req.spec.mandatory,
            tolerance=req.spec.tolerance,
            label=req.spec.label,
            raw_text=req.spec.raw_text,
            evidence_document_id=prov.document_id,
            page=prov.page,
        )

    # Bids + supplier rows + field evidence -----------------------------------
    for supplier_name, ext_bid in extraction.bids.items():
        bid = ext_bid.bid
        supplier = None
        for existing in store.list_suppliers(db, case_id):
            if existing.name.strip().lower() == supplier_name.strip().lower():
                supplier = existing
                break
        if supplier is None:
            supplier = store.create_supplier(db, case_id=case_id, name=supplier_name,
                                             source_document_id=ext_bid.provenance[0].document_id
                                             if ext_bid.provenance else None)
        supplier_ids[supplier_name] = supplier.id
        bid_evidence[supplier_name] = {}

        for prov in ext_bid.provenance:
            field = prov.section or "certification"
            ev = store.add_evidence(
                db,
                case_id=case_id,
                document_id=prov.document_id,
                document_name=doc_label(prov.document_id),
                supplier_id=supplier.id,
                field=field,
                value=_value_for_field(bid, field),
                text=prov.text,
                page=prov.page,
                section=prov.section,
                confidence=ext_bid.confidence,
                doc_type=docs_by_id.get(prov.document_id).doc_type if prov.document_id in docs_by_id else "other",
            )
            bid_evidence[supplier_name].setdefault(field, []).append(ev.id)

        store.upsert_quote(
            db,
            case_id=case_id,
            supplier_name=supplier.name,
            supplier_id=supplier.id,
            values={
                "source_document_ids": list({p.document_id for p in ext_bid.provenance}),
                "material": bid.material,
                "quantity": bid.quantity,
                "quantity_unit": bid.quantity_unit,
                "price": bid.price,
                "currency": bid.currency,
                "delivery_days": bid.delivery_days,
                "payment_terms": bid.payment_terms,
                "warranty_months": bid.warranty_months,
                "bid_validity_days": bid.bid_validity_days,
                "certifications": [c.name for c in bid.certifications],
                "confidence": ext_bid.confidence,
            },
        )

    # Certificates -------------------------------------------------------------
    for cert in extraction.certifications:
        supplier_id = None
        for name, sid in supplier_ids.items():
            if name.strip().lower() == cert.supplier_name.strip().lower():
                supplier_id = sid
                break
        prov = cert.provenance
        ev = store.add_evidence(
            db,
            case_id=case_id,
            document_id=prov.document_id,
            document_name=doc_label(prov.document_id),
            supplier_id=supplier_id,
            field="certification",
            value=cert.name,
            text=prov.text,
            page=prov.page,
            section=prov.section,
            confidence=cert.confidence,
            doc_type="certificate",
        )
        if supplier_id:
            bid_evidence.setdefault(cert.supplier_name, {}).setdefault("certification", []).append(ev.id)

    # Policy clauses -----------------------------------------------------------
    for clause in extraction.policy_clauses:
        prov = clause.provenance
        store.add_evidence(
            db,
            case_id=case_id,
            document_id=prov.document_id,
            document_name=doc_label(prov.document_id),
            field="policy",
            value={"clause": clause.clause, "requirement_field": clause.requirement_field,
                   "value": clause.value},
            text=prov.text,
            page=prov.page,
            section=prov.section,
            confidence=clause.confidence,
            doc_type="policy",
        )

    db.commit()
    return {"supplier_ids": supplier_ids, "bid_evidence": bid_evidence,
            "requirements": len(extraction.requirements),
            "bids": len(extraction.bids), "certs": len(extraction.certifications)}


def _value_for_field(bid: SupplierBid, field: str) -> object:
    if field == "certification":
        return [c.name for c in bid.certifications]
    mapping = {
        "material": bid.material,
        "quantity": bid.quantity,
        "price": bid.price,
        "delivery_days": bid.delivery_days,
        "payment_terms": bid.payment_terms,
        "warranty_months": bid.warranty_months,
        "bid_validity_days": bid.bid_validity_days,
    }
    return mapping.get(field)


# ---------------------------------------------------------------------------
# Build requirement specs & bids for the verifier
# ---------------------------------------------------------------------------

def apply_policy(db: Session, case_id: str) -> int:
    """Turn persisted policy evidence into Requirement rows (idempotent).

    Policy clauses that map to verifiable fields become extra requirements;
    anything else is retained as evidence only. Returns the number added.
    """
    clauses_ev = store.list_evidence(db, case_id, field="policy")
    existing_rows = store.list_requirements(db, case_id)
    existing_keys = {(r.field, r.raw_text) for r in existing_rows}
    added = 0
    for ev in clauses_ev:
        clause = ev.value if isinstance(ev.value, dict) else {}
        field = clause.get("requirement_field")
        value = clause.get("value")
        text = clause.get("clause") or ""
        if not field or value is None:
            continue
        if (field, text) in existing_keys:
            continue
        store.add_requirement(
            db,
            case_id=case_id,
            field=field,
            operator="lte" if field == "price" else "in" if field == "certification" else "eq",
            value=value,
            mandatory=True if field == "certification" else False,
            label=f"policy.{field}",
            raw_text=text,
            evidence_document_id=ev.document_id,
            page=ev.page,
        )
        existing_keys.add((field, text))
        added += 1
    return added


def build_requirement_specs(db: Session, case_id: str) -> list[RequirementSpec]:
    """Requirements from DB (RFQ + policy-derived rows)."""
    specs: list[RequirementSpec] = []
    for row in store.list_requirements(db, case_id):
        value = row.value if isinstance(row.value, dict) else {"v": row.value}
        payload = value.get("v", value.get("value", row.value))
        spec = RequirementSpec(
            field=row.field,
            operator=row.operator,
            value=payload,
            mandatory=row.mandatory,
            unit=row.unit,
            currency=row.currency,
            tolerance=row.tolerance,
            label=row.label,
            raw_text=row.raw_text,
        )
        if row.id:
            spec.evidence_id = row.id
        specs.append(spec)
    return specs


def build_bids(db: Session, case_id: str, extraction: ExtractionResult | None = None) -> list[SupplierBid]:
    """Merge DB quotes with certification info (including person certificates)."""
    quotes = store.list_quotes(db, case_id)
    bids: list[SupplierBid] = []
    for q in quotes:
        certs: list[CertificationInfo] = []
        for name in q.certifications or []:
            cert = CertificationInfo(name=name)
            certs.append(cert)

        if extraction:
            for cert in extraction.certifications:
                if cert.supplier_name.strip().lower() == q.supplier_name.strip().lower():
                    existing = next((c for c in certs if c.name == cert.name), None)
                    if existing:
                        existing.issue_date = cert.issue_date
                        existing.expiry_date = cert.expiry_date
                    else:
                        certs.append(CertificationInfo(name=cert.name, issue_date=cert.issue_date,
                                                       expiry_date=cert.expiry_date))

        bids.append(SupplierBid(
            supplier_name=q.supplier_name,
            material=q.material,
            quantity=q.quantity,
            quantity_unit=q.quantity_unit,
            price=q.price,
            currency=q.currency,
            delivery_days=q.delivery_days,
            certifications=certs,
            payment_terms=q.payment_terms,
            warranty_months=q.warranty_months,
            bid_validity_days=q.bid_validity_days,
        ))
    return bids


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_case(db: Session, case_id: str,
                requirements: list[RequirementSpec], bids: list[SupplierBid],
                case_date: date | None = None) -> list[dict]:
    """Run the deterministic engine over all bids; persist + return evaluations."""
    context = VerifierInput(
        case_date=case_date,
        default_currency=settings.default_currency,
        fx_rates=settings_fx_rates(),
    )
    supplier_ids = {s.name.strip().lower(): s.id for s in store.list_suppliers(db, case_id)}
    results: list[dict] = []
    for bid in bids:
        evaluation = evaluate_supplier(requirements, bid, context)
        status = evaluation.outcome.value
        results.append({
            "supplier_id": supplier_ids.get(bid.supplier_name.strip().lower()),
            "supplier_name": bid.supplier_name,
            "passed": evaluation.passed,
            "score": evaluation.score,
            "status": status,
            "checks": [c.model_dump(mode="json") for c in evaluation.checks],
            "rejection_reasons": evaluation.rejection_reasons,
            "unknowns": evaluation.unknowns,
            "risks": evaluation.risks,
            "mandatory_passed": evaluation.mandatory_passed,
            "price_converted": _price_in_default_currency(bid, context),
        })
        rec = store.save_evaluation(
            db,
            case_id=case_id,
            supplier_name=bid.supplier_name,
            supplier_id=supplier_ids.get(bid.supplier_name.strip().lower()),
            passed=evaluation.passed,
            score=evaluation.score,
            status=status,
            checks=[c.model_dump(mode="json") for c in evaluation.checks],
            rejection_reasons=evaluation.rejection_reasons,
            risks=evaluation.risks,
        )
    db.commit()
    return results


def _price_in_default_currency(bid: SupplierBid, context: VerifierInput) -> float | None:
    """Bid price expressed in the requirement currency (for ranking tie-breaks)."""
    if bid.price is None:
        return None
    default = normalize_currency(context.default_currency)
    bid_currency = normalize_currency(bid.currency)
    if default and bid_currency and default != bid_currency:
        rate = context.fx_rates.get(bid_currency)
        if rate:
            return bid.price * rate
        return None
    return bid.price


def settings_fx_rates() -> dict[str, float]:
    """Static reference rates (configurable) used when converting foreign bids."""
    return {
        "EUR": 95.0,
        "USD": 84.0,
        "GBP": 110.0,
        "AED": 23.0,
        "SGD": 63.0,
    }


# ---------------------------------------------------------------------------
# Ranking & recommendation (Decision Agent core)
# ---------------------------------------------------------------------------

def _rank_price(ev: dict) -> float:
    price = ev.get("price_converted")
    return price if price is not None else float("inf")


def rank_suppliers(evaluations: list[dict]) -> list[dict]:
    """Rank compliant suppliers by score then price tie-break; non-compliant below."""
    def sort_key(ev: dict) -> tuple:
        compliant = 0 if ev["passed"] else 1
        price = 0.0 if ev["passed"] else float("inf")
        if ev["passed"]:
            price = _rank_price(ev)
        return (compliant, -ev["score"], price)

    ranked = sorted(evaluations, key=sort_key)
    for idx, ev in enumerate(ranked):
        ev["rank"] = idx + 1
    return ranked


def build_recommendation(db: Session, case_id: str, evaluations: list[dict],
                         extraction: ExtractionResult | None = None) -> dict:
    """Produce the final recommendation with reasons, rejections and unknowns.

    Rules:
    - Only fully-passed suppliers are recommendable.
    - The cheapest valid supplier wins (equal scores) — mandatory failures never
      allow a cheaper supplier to pass.
    - If no compliant supplier exists but some are only UNVERIFIED (not FAIL),
      we abstain (status=insufficient) rather than inventing a recommendation.
    """
    passed = [e for e in evaluations if e["passed"]]
    others = [e for e in evaluations if not e["passed"]]

    recommended = None
    status = "recommended"
    overall_score = 0.0
    reasons: list[str] = []
    rejections: list[dict] = []
    unknowns: list[str] = []
    risks: list[str] = []

    if passed:
        sorted_passed = sorted(passed, key=lambda e: (-e["score"], _rank_price(e)))
        best_ev = sorted_passed[0]
        recommended = best_ev["supplier_name"]
        overall_score = best_ev["score"]
        reasons = [c["reason"] for c in best_ev["checks"] if c["status"] == "PASS"]
        risks = best_ev["risks"] or []
        for ev in sorted_passed[1:]:
            reasons.append(f"{ev['supplier_name']} also compliant (score {ev['score']}).")
    else:
        # No compliant supplier. If any evaluation hit an evidence gap we must
        # abstain (insufficient) rather than declare a definitive no-valid.
        status = (
            "insufficient"
            if any(e["unknowns"] for e in evaluations)
            else "no_valid"
        )

    for ev in others:
        rejections.append({
            "supplier_name": ev["supplier_name"],
            "status": ev["status"],
            "rejection_reasons": ev["rejection_reasons"],
            "unknowns": ev["unknowns"],
            "risks": ev["risks"],
            "score": ev["score"],
        })
        unknowns.extend(ev["unknowns"])
        risks.extend(ev["risks"])

    confidence = _recommendation_confidence(evaluations, recommended)

    if confidence < settings.recommendation_confidence_threshold:
        status = "insufficient" if passed else status

    ranked = rank_suppliers(evaluations)

    rec = store.save_recommendation(
        db,
        case_id=case_id,
        recommended_supplier=recommended,
        overall_score=overall_score,
        confidence=confidence,
        status=status,
        summary=_summary(recommended, status, passed),
        reasons=reasons,
        rejections=rejections,
        unknowns=list(dict.fromkeys(unknowns)),
        risks=list(dict.fromkeys(risks)),
        ranked_suppliers=ranked,
    )
    db.commit()
    return {
        "id": rec.id,
        "case_id": case_id,
        "recommended_supplier": recommended,
        "overall_score": overall_score,
        "confidence": confidence,
        "status": status,
        "summary": rec.summary,
        "reasons": reasons,
        "rejections": rejections,
        "unknowns": list(dict.fromkeys(unknowns)),
        "risks": list(dict.fromkeys(risks)),
        "ranked_suppliers": ranked,
    }


def _recommendation_confidence(evaluations: list[dict], recommended: str | None) -> float:
    if not recommended:
        return 0.0
    ev = next((e for e in evaluations if e["supplier_name"] == recommended), None)
    if ev is None:
        return 0.0
    base = ev["score"] / 100.0
    # discount for unknowns / missing evidence
    discount = 0.15 * len([c for c in ev["checks"] if c["status"] == "UNVERIFIED"])
    return round(max(0.0, min(1.0, base - discount)), 3)


def _summary(recommended: str | None, status: str, passed: list[dict]) -> str:
    if recommended and status != "insufficient":
        return (
            f"RECOMMENDED: {recommended}. This supplier satisfies all mandatory requirements "
            "with full evidence. Commercial and technical constraints verified."
        )
    if status == "no_valid":
        return "No supplier satisfies all mandatory requirements. Recommendations withheld."
    return (
        "Insufficient evidence to recommend a supplier. "
        "Do not derive conclusions from incomplete data."
    )


# ---------------------------------------------------------------------------
# Full deterministic run (used by orchestrator and tests)
# ---------------------------------------------------------------------------

def run_case_analysis(db: Session, case_id: str, request_id: str | None = None) -> dict:
    start = time.monotonic()
    request_id = request_id or uuid.uuid4().hex
    case = store.get_case(db, case_id)
    if case is None:
        raise ValueError(f"Case {case_id} not found.")
    store.set_case_status(db, case_id, "analyzing")
    db.commit()

    documents = store.list_documents(db, case_id)
    extraction = analyze_case_documents(documents)
    persist_extraction(db, case_id, extraction)
    apply_policy(db, case_id)

    requirements = build_requirement_specs(db, case_id)
    bids = build_bids(db, case_id, extraction)
    if not bids:
        store.set_case_status(db, case_id, "failed")
        db.commit()
        return {"case_id": case_id, "status": "failed", "error": "No supplier quotes extracted."}

    evaluations = verify_case(db, case_id, requirements, bids,
                              case_date=case.metadata_json.get("case_date") or None)
    recommendation = build_recommendation(db, case_id, evaluations, extraction)

    from backend.app.services.memory_writer import write_case_memory  # lazy to avoid cycles
    write_case_memory(db, case_id, recommendation)

    store.set_case_status(db, case_id, "completed")
    db.commit()
    return {
        "case_id": case_id,
        "request_id": request_id,
        "status": "completed",
        "duration_ms": int((time.monotonic() - start) * 1000),
        "requirements": [r.model_dump(mode="json") for r in requirements],
        "bids": [b.model_dump(mode="json") for b in bids],
        "evaluations": evaluations,
        "recommendation": recommendation,
    }