"""Document analyzer: turns pages/tables into structured requirements and bids.

Deterministic-first extraction. Every candidate value keeps provenance. If a
field cannot be established it is simply absent (the verifier then reports
UNVERIFIED rather than guessing).

Supplier resolution:
- explicit name in the document (``Supplier``/``Vendor``/``M/s`` header)
- fallback to the filename (``supplier_a_quote.pdf`` -> Supplier A)
- else generated from the filename.
"""

from __future__ import annotations

import re
from typing import Optional

from backend.app.config import settings
from backend.app.models.procurement import CaseDocument
from backend.app.services.extraction import heuristics as h
from backend.app.services.extraction.models import (
    ExtractedBid,
    ExtractedCertification,
    ExtractedPolicyClause,
    ExtractedRequirement,
    ExtractionResult,
    Provenance,
)
from backend.app.services.ingestion import read_page_text, read_tables
from backend.app.services.verifier import CertificationInfo, RequirementSpec, SupplierBid

_MATERIAL_REQUIRED_NOUNS = ["material", "grade", "specif"]
_QUANTITY_REQUIRED_NOUNS = ["quantity", "qty", "qty.", "nos", "number"]
_BUDGET_NOUNS = ["budget", "maximum budget", "max budget", "estimated value", "anticipated value",
                 "not exceeding", "price limit", "ceiling"]

_FILENAME_SUPPLIER_RE = re.compile(
    r"(?:supplier|vendor|s)[_\-\s]+([a-z0-9]+)", re.IGNORECASE
)


def _provenance(doc: CaseDocument, page: int | None, raw: str, method="heuristic") -> Provenance:
    return Provenance(
        document_id=doc.id,
        document_name=doc.filename,
        page=page,
        text=raw,
        method=method,
    )


def _document_texts(doc: CaseDocument) -> list[tuple[int, str]]:
    """Return [(page_no, text_with_tables), ...]."""
    pages = read_page_text(doc)
    tables = read_tables(doc)
    out: list[tuple[int, str]] = []
    for page_no, page_text in enumerate(pages, start=1):
        text = page_text
        for table in tables:
            if table.get("page") == page_no:
                rows = table.get("table", [])
                text = f"{text}\n\n" + "\n".join(
                    " | ".join(c or "" for c in row) for row in rows
                )
        out.append((page_no, text))
    return out


def _supplier_from_filename(filename: str) -> Optional[str]:
    m = _FILENAME_SUPPLIER_RE.search(filename)
    if m:
        return f"Supplier {m.group(1).title()}"
    core = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    if core and core.lower().startswith(("supplier ", "vendor ")):
        return core[:40]
    return None


def _resolve_supplier(doc: CaseDocument, full_text: str, filename_hint: Optional[str]) -> str:
    for page_text_chunk in [full_text[:4000]]:
        m = h._SUPPLIER_RE.search(page_text_chunk)
        if m:
            return m.group(1).strip().rstrip(",;")[:60]
    filename_supplier = _supplier_from_filename(doc.filename) or (filename_hint or "")
    return filename_supplier[:60] or f"Supplier ({doc.filename})"


# ---------------------------------------------------------------------------
# Requirement extraction from RFQ / spec documents
# ---------------------------------------------------------------------------

def _extract_requirements(doc: CaseDocument, texts: list[tuple[int, str]]) -> list[ExtractedRequirement]:
    reqs: list[ExtractedRequirement] = []
    seen: set[str] = set()

    def record(spec: RequirementSpec, prov: Provenance, confidence: float, key: str) -> None:
        if key in seen:
            return
        seen.add(key)
        reqs.append(ExtractedRequirement(spec=spec, provenance=prov, confidence=confidence))

    for page_no, text in texts:
        lower = text.lower()
        # Material ----------------------------------------------------------
        mat = h.extract_material(text)
        if mat and any(n in lower for n in _MATERIAL_REQUIRED_NOUNS):
            near = text[max(0, lower.find("material") - 30): lower.find("material") + 60]
            mandatory = h.detect_mandatory(near) or "must" in lower[:2000]
            record(
                RequirementSpec(field="material", operator="eq", value=mat.value,
                                mandatory=True, label="material",
                                raw_text=mat.raw_text),
                _provenance(doc, page_no, mat.raw_text),
                mat.confidence,
                f"material:{page_no}",
            )
        # Quantity ----------------------------------------------------------
        qty = h.extract_quantity(text)
        if qty and any(n in lower for n in _QUANTITY_REQUIRED_NOUNS):
            unit = qty.extra.get("unit")
            near = text[max(0, lower.find(qty.raw_text[:20].lower() if qty.raw_text else "qty") - 40):]
            mandatory = (h.detect_mandatory(near) or "must" in lower[:2000] or
                         any(u in lower for u in ["shall", "required"]))
            record(
                RequirementSpec(field="quantity", operator="gte", value=qty.value,
                                unit=unit, mandatory=True, label="quantity",
                                raw_text=qty.raw_text),
                _provenance(doc, page_no, qty.raw_text),
                qty.confidence,
                f"quantity:{page_no}",
            )
        # Budget / max price -------------------------------------------------
        budget = h.extract_budget(text)
        if budget and any(n in lower for n in _BUDGET_NOUNS):
            record(
                RequirementSpec(field="price", operator="lte", value=budget.value,
                                currency=budget.extra.get("currency") or settings.default_currency,
                                mandatory=True, label="max_price", raw_text=budget.raw_text),
                _provenance(doc, page_no, budget.raw_text),
                budget.confidence,
                "price",
            )
        # Delivery -----------------------------------------------------------
        delivery = h.extract_delivery(text)
        if delivery:
            record(
                RequirementSpec(field="delivery_days", operator="lte", value=delivery.value,
                                mandatory=True, label="max_delivery", raw_text=delivery.raw_text),
                _provenance(doc, page_no, delivery.raw_text),
                delivery.confidence,
                "delivery",
            )
        # Certifications ------------------------------------------------------
        for cert in h.extract_certifications(text):
            if "certif" in lower:
                record(
                    RequirementSpec(field="certification", value=cert.value,
                                    mandatory=True, label="certification",
                                    raw_text=cert.raw_text),
                    _provenance(doc, page_no, cert.raw_text),
                    cert.confidence,
                    f"certification:{cert.value}",
                )
    return reqs


# ---------------------------------------------------------------------------
# Quote extraction
# ---------------------------------------------------------------------------

def _extract_quote(doc: CaseDocument, texts: list[tuple[int, str]],
                   known_supplier: str | None) -> ExtractedBid | None:
    full_text = "\n".join(t for _, t in texts)
    supplier_name = _resolve_supplier(doc, full_text, known_supplier)

    material: Optional[str] = None
    quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    delivery_days: Optional[int] = None
    delivery_text: Optional[str] = None
    payment: Optional[str] = None
    warranty: Optional[int] = None
    bid_validity: Optional[int] = None
    certifications: list[CertificationInfo] = []
    provenances: list[Provenance] = []

    for page_no, text in texts:
        m = h.extract_material(text)
        if m and material is None:
            material = m.value
            provenances.append(_with_field(_provenance(doc, page_no, m.raw_text), "material"))

        q = h.extract_quantity(text)
        if q and quantity is None:
            quantity = q.value
            quantity_unit = q.extra.get("unit")
            provenances.append(_with_field(_provenance(doc, page_no, q.raw_text), "quantity"))

        p = h.extract_price(text)
        if p:
            # prefer the largest price that looks like a total
            if price is None or (p.value > price):
                price = p.value
                currency = p.extra.get("currency") or "INR"
                provenances.append(_with_field(_provenance(doc, page_no, p.raw_text), "price"))

        d = h.extract_delivery(text)
        if d and delivery_days is None:
            delivery_days = int(d.value)
            delivery_text = d.raw_text
            provenances.append(_with_field(_provenance(doc, page_no, d.raw_text), "delivery_days"))

        pay = h.extract_payment_terms(text)
        if pay and payment is None:
            payment = pay.value
            provenances.append(_with_field(_provenance(doc, page_no, pay.raw_text), "payment_terms"))

        w = h.extract_warranty(text)
        if w and w.value is not None and warranty is None:
            warranty = int(w.value)
            provenances.append(_with_field(_provenance(doc, page_no, w.raw_text), "warranty_months"))

        bv = h.extract_bid_validity(text)
        if bv and bv.value is not None and bid_validity is None:
            bid_validity = int(bv.value)
            provenances.append(_with_field(_provenance(doc, page_no, bv.raw_text), "bid_validity_days"))

        for cert in h.extract_certifications(text):
            if all(c.name != cert.value for c in certifications):
                certifications.append(CertificationInfo(name=cert.value))
            provenances.append(_with_field(_provenance(doc, page_no, cert.raw_text), "certification"))

    bid = SupplierBid(
        supplier_name=supplier_name,
        material=material,
        quantity=quantity,
        quantity_unit=quantity_unit,
        price=price,
        currency=currency,
        delivery_days=delivery_days,
        delivery_text=delivery_text,
        certifications=certifications,
        payment_terms=payment,
        warranty_months=warranty,
        bid_validity_days=bid_validity,
    )
    confidence = _aggregate_confidence(provenances)
    return ExtractedBid(supplier_name=supplier_name, bid=bid, provenance=provenances,
                        confidence=confidence)


def _with_field(prov: Provenance, field: str) -> Provenance:
    return Provenance(**{**prov.model_dump(), "section": field})


def _aggregate_confidence(provenances: list[Provenance]) -> float:
    if not provenances:
        return 0.3
    return round(min(1.0, 0.5 + 0.1 * len(provenances)), 2)


# ---------------------------------------------------------------------------
# Certificate extraction
# ---------------------------------------------------------------------------

def _extract_certificates(doc: CaseDocument, texts: list[tuple[int, str]]) -> list[ExtractedCertification]:
    results: list[ExtractedCertification] = []
    full_text = "\n".join(t for _, t in texts)
    supplier_name = _resolve_supplier(doc, full_text, None)

    for page_no, text in texts:
        for cert in h.extract_certifications(text):
            expiry = issue = None
            ev = h.extract_cert_validity(text)
            if ev and ev.value is not None:
                expiry = ev.value
            iss = h.extract_issue_date(text)
            if iss and iss.value is not None:
                issue = iss.value
            results.append(
                ExtractedCertification(
                    supplier_name=supplier_name,
                    name=cert.value,
                    issue_date=issue,
                    expiry_date=expiry,
                    provenance=_provenance(doc, page_no, cert.raw_text),
                    confidence=0.95,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Policy extraction
# ---------------------------------------------------------------------------

_POLICY_RE = re.compile(
    r"(?:purchase|procurement|order|contract)[s]?\s+(?:above|exceeding|over|greater than|up to)\s*"
    r"(?:₹|Rs\.?|INR)?\s*(?P<amount>[\d,]+(?:\.\d+)?)\s*(?:lakh|crore|cr|k)?",
    re.IGNORECASE,
)
_POLICY_MAND_CERT = re.compile(r"(mandatory|required)\s*(?:certification|certificate)\s*:?\s*([A-Z0-9 .]+)", re.I)
_POLICY_PROHIBITED = re.compile(r"(prohibited|not allowed|banned)\s*:?\s*([^\n]{5,80})", re.I)


def _extract_policy(doc: CaseDocument, texts: list[tuple[int, str]]) -> list[ExtractedPolicyClause]:
    clauses: list[ExtractedPolicyClause] = []
    for page_no, text in texts:
        for m in _POLICY_RE.finditer(text):
            amount = h.parse_number(m.group("amount"))
            if amount is None:
                continue
            clauses.append(ExtractedPolicyClause(
                clause=f"Approval required above {amount}",
                requirement_field="price", value=amount, applicability="all",
                provenance=_provenance(doc, page_no, m.group(0)),
            ))
        for m in _POLICY_MAND_CERT.finditer(text):
            cert_name = m.group(2).strip()
            clauses.append(ExtractedPolicyClause(
                clause=f"Mandatory certification {cert_name}",
                requirement_field="certification", value=cert_name, applicability="all",
                provenance=_provenance(doc, page_no, m.group(0)),
            ))
        for m in _POLICY_PROHIBITED.finditer(text):
            clauses.append(ExtractedPolicyClause(
                clause=f"Prohibited: {m.group(2).strip()}", applicability="all",
                provenance=_provenance(doc, page_no, m.group(0)),
            ))
    return clauses


# ---------------------------------------------------------------------------
# Top-level analysis
# ---------------------------------------------------------------------------

def analyze_document(doc: CaseDocument, known_supplier: str | None = None) -> ExtractionResult | None:
    """Run extraction over a single document.

    Returns contribution to an ExtractionResult depending on document type.
    """
    texts = _document_texts(doc)
    if not texts:
        return None

    if doc.doc_type in ("rfq", "spec"):
        reqs = _extract_requirements(doc, texts)
        return ExtractionResult(requirements=reqs)

    if doc.doc_type == "quote":
        bid = _extract_quote(doc, texts, known_supplier)
        result = ExtractionResult()
        if bid:
            result.bids[bid.supplier_name] = bid
        return result

    if doc.doc_type == "certificate":
        certs = _extract_certificates(doc, texts)
        return ExtractionResult(certifications=certs)

    if doc.doc_type == "policy":
        clauses = _extract_policy(doc, texts)
        return ExtractionResult(policy_clauses=clauses)

    return ExtractionResult()


def analyze_case_documents(documents: list[CaseDocument]) -> ExtractionResult:
    """Analyze all documents of a case and merge into one ExtractionResult."""
    result = ExtractionResult()
    seen_reqs: set[tuple] = set()
    for doc in documents:
        contribution = analyze_document(doc)
        if contribution is None:
            continue
        for req in contribution.requirements:
            spec = req.spec
            key = (spec.field, spec.operator,
                   str(spec.value) if spec.value is not None else "",
                   spec.unit or "", spec.currency or "")
            if key in seen_reqs:
                continue
            seen_reqs.add(key)
            result.requirements.append(req)
        for name, bid in contribution.bids.items():
            result.bids[name] = bid
        result.certifications.extend(contribution.certifications)
        result.policy_clauses.extend(contribution.policy_clauses)
    return result