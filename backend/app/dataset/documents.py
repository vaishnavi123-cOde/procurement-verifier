"""ReportLab document builders for the synthetic benchmark.

Renders one PDF per authored document type: RFQ, specification, supplier quote,
procurement policy, certificates, and optional supplier history.

Formatting is intentionally varied (Indian digit grouping, lakh notation,
weeks-based delivery, missing fields) so extraction must rely on semantics
rather than a single layout.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from backend.app.dataset.manifests import CaseManifest
from backend.app.dataset.pricing import format_amount, format_inr, format_lakh

AUTHORITY = "National Infrastructure Procurement Agency"
AUTHORITY_REF = "NIPA"

_STYLES = getSampleStyleSheet()
_BODY = ParagraphStyle("Body", parent=_STYLES["BodyText"], leading=13, spaceAfter=6)
_H2 = ParagraphStyle("H2", parent=_STYLES["Heading2"], spaceBefore=10, spaceAfter=6)
_H1 = ParagraphStyle("H1", parent=_STYLES["Heading1"], fontSize=14, spaceAfter=4)
_SMALL = ParagraphStyle("Small", parent=_STYLES["BodyText"], fontSize=9.5, leading=12)

_TABLE_HEADER = ("Helvetica-Bold", 10)
_TABLE_BODY = ("Helvetica", 10)

_CUR_SYMBOL = {"INR": "Rs.", "USD": "$", "EUR": "EUR ", "GBP": "£"}


def _style_table(rows: list[list], col_widths: list[float] | None = None,
                 header: bool = True) -> Table:
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")))
        style.append(("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    return t


def _doc(path: Path, flowables, title: str, subtitle: str = "") -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=title, author="Procurement Benchmark (synthetic)")
    story = [
        Paragraph(f"{AUTHORITY} — {AUTHORITY_REF}", _H1),
        Paragraph(subtitle, _SMALL) if subtitle else Spacer(1, 1),
        Spacer(1, 4),
    ]
    story.extend(flowables)
    doc.build(story)


def _lakh_or_indian(value: float, currency: str, style: str) -> str:
    if currency == "INR" and style == "lakh":
        return f"Rs. {format_lakh(value)}"
    if currency == "INR" and style == "indian":
        return f"Rs. {format_inr(value)}"
    return format_amount(value, currency)


def render_rfq(path: Path, manifest: CaseManifest, reqs, material_name: str) -> None:
    rows = [["#", "Requirement", "Specification"]]
    row_map = {
        "material": ("Material of construction", f"{material_name} (as per specification)"),
        "quantity": ("Quantity", _qty_spec(reqs)),
        "price": ("Price ceiling", _price_spec(reqs)),
        "delivery_days": ("Delivery", _delivery_spec(reqs)),
        "certification": ("Mandatory certification", ", ".join(sorted(_cert_values(reqs)))),
    }
    for i, r in enumerate(reqs, start=1):
        if r.field in row_map:
            rows.append([str(i), row_map[r.field][0], row_map[r.field][1]])

    budget = _budget_of(reqs)
    budget_line = f"Budget : Rs. {format_inr(budget)}" if budget else ""
    qty = _qty_of(reqs)
    qty_line = f"Quantity : {qty[0]:g} {qty[1]}" if qty else ""
    delivery = _delivery_of(reqs)
    cert = _first_cert(reqs)

    flow = [
        Paragraph(f"Tender Reference : NIPA/{manifest.case_id.upper()}/2025", _BODY),
        Paragraph(f"Issue Date : {manifest.case_date.strftime('%d %b %Y')}", _BODY),
        Paragraph(f"Procurement of {material_name}", _H2),
        Paragraph(
            "The {0} invites sealed offers for the supply of {1}. The bidder shall "
            "furnish a complete quotation with material, quantity, unit price, total "
            "price, delivery schedule, and certifications.".format(AUTHORITY, material_name),
            _BODY,
        ),
        Paragraph("Scope of Supply", _H2),
        _style_table(rows, col_widths=[12 * mm, 70 * mm, 95 * mm]),
        Spacer(1, 8),
        Paragraph("Terms", _H2),
        Paragraph(f"Estimated Value : {budget_line}" if budget_line else "", _BODY),
        Paragraph(f"Required Quantity : {qty[0]:g} {qty[1]}" if qty_line else "", _BODY),
        Paragraph(f"Delivery : within {delivery} days of order" if delivery else "", _BODY),
        Paragraph(
            f"The bidder shall mandatorily hold {cert} certification. Materials, quantities "
            "and prices offered must be clearly stated.", _BODY),
        Paragraph(
            "Payment Terms : 30 days after receipt and acceptance of goods.",
            _BODY),
    ]
    _doc(path, flow, "Request For Quotation", f"{manifest.case_id} — request for quotation")


def render_spec(path: Path, manifest: CaseManifest, reqs, material_name: str, grade: str) -> None:
    spec_rows = [["Property", "Requirement"]]
    spec_rows.append(["Material grade", grade])
    spec_rows.append(["Standard", "As per applicable international / national standard"])
    spec_rows.append(["Workmanship", "Defect free; no scale, laminations or sharp edges"])
    spec_rows.append(["Packaging", "Wooden crates / pallets as appropriate"])
    flow = [
        Paragraph(f"Technical Specification — {material_name}", _H2),
        Paragraph("This specification is part of the tender and is binding on all bidders.",
                  _BODY),
        _style_table(spec_rows, col_widths=[70 * mm, 107 * mm]),
        Spacer(1, 8),
        Paragraph(
            f"The material of construction shall be {grade}. Material certificates matching "
            "the grade above must accompany the goods.", _BODY),
    ]
    _doc(path, flow, "Technical Specification", f"{manifest.case_id} — specification")


def render_quote(path: Path, manifest: CaseManifest, bid) -> None:
    symbol = _CUR_SYMBOL.get(bid.currency, f"{bid.currency} ")
    rows = [["#", "Item", "Specification", "Quantity", "Unit", "Unit Price"]]
    qty_text = f"{bid.quantity:g} {bid.quantity_unit}"
    unit_rate = 0.0
    if bid.total_price is not None and bid.quantity:
        unit_rate = round(bid.total_price / bid.quantity, 2)
    rows.append(["1", "Supply of goods as per tender", bid.material, f"{bid.quantity:g}",
                 bid.quantity_unit, f"{symbol}{unit_rate:,.2f}"])

    style = bid.formatting.get("price_style", "indian")
    flow = [
        Paragraph(f"Supplier Name : {bid.supplier}", _BODY),
        Paragraph(f"Quotation Ref : Q-{manifest.case_id.upper()}", _BODY),
        Paragraph(f"Date : {manifest.case_date.strftime('%d %b %Y')}", _BODY),
        Paragraph("Quotation", _H2),
        _style_table(rows, col_widths=[10 * mm, 60 * mm, 40 * mm, 24 * mm, 20 * mm, 28 * mm]),
        Spacer(1, 8),
    ]
    if bid.total_price is not None:
        total_txt = _lakh_or_indian(bid.total_price, bid.currency, style)
        flow.append(Paragraph(f"Total Price : {total_txt}", _BODY))
    flow.append(Paragraph(f"Quantity : {bid.quantity:g} {bid.quantity_unit}", _BODY))
    if bid.delivery_days and not bid.delivery_text:
        flow.append(Paragraph(f"Delivery : within {bid.delivery_days} days", _BODY))
    elif bid.delivery_text:
        flow.append(Paragraph(f"Delivery : {bid.delivery_text}", _BODY))
    if bid.payment_terms:
        flow.append(Paragraph(f"Payment Terms : {bid.payment_terms}", _BODY))
    if bid.bid_validity:
        flow.append(Paragraph(f"Bid Validity : {bid.bid_validity} days", _BODY))
    if bid.warranty_months:
        flow.append(Paragraph(f"Warranty : {bid.warranty_months} months", _BODY))
    if bid.certs:
        flow.append(Paragraph("Certifications : " + ", ".join(c.name for c in bid.certs), _BODY))
    _doc(path, flow, "Quotation", f"{manifest.case_id} — {bid.supplier}")


def render_policy(path: Path, manifest: CaseManifest, policy, budget: float | None) -> None:
    flow = [
        Paragraph("Procurement Policy", _H2),
        Paragraph(
            "This policy governs all purchases executed under this authority.",
            _BODY),
    ]
    if policy.approval_threshold:
        flow.append(Paragraph(
            f"Any purchase order exceeding Rs. {format_inr(policy.approval_threshold)} "
            "shall require prior written approval of the competent authority.", _BODY))
    if policy.mandatory_certs:
        flow.append(Paragraph(
            "Mandatory Certification : " + ", ".join(policy.mandatory_certs), _BODY))
    flow.append(Paragraph(
        "Suppliers found to hold prohibited business relationships shall not be awarded "
        "any order.", _BODY))
    _doc(path, flow, "Procurement Policy", f"{manifest.case_id} — policy")


def render_certificate(path: Path, manifest: CaseManifest, bid, cert) -> None:
    expiry_txt = cert.expiry.strftime("%d %b %Y") if cert.expiry else "Not specified"
    issue_txt = cert.issue.strftime("%d %b %Y") if cert.issue else "Not specified"
    flow = [
        Paragraph(f"Supplier : {bid.supplier}", _BODY),
        Paragraph(f"Certificate of Conformance — {cert.name}", _H2),
        Paragraph(f"Standard : {cert.name}", _BODY),
        Paragraph(f"Certificate No. : CERT-{manifest.case_id.upper()}-{cert.name.replace(' ', '_')}", _BODY),
        Paragraph(f"Issue Date : {issue_txt}", _BODY),
        Paragraph(f"Valid up to : {expiry_txt}", _BODY),
        Paragraph("This certificate verifies conformance to the stated management system "
                  "standard.", _BODY),
    ]
    _doc(path, flow, "Certificate", f"{manifest.case_id} — {bid.supplier}")


def render_history(path: Path, manifest: CaseManifest, bid, material_name: str) -> None:
    rows = [["Order Ref", "Material", "Qty", "Value", "Year"]]
    anchor_price = bid.total_price if bid.total_price else 100000.0
    for j in range(3):
        year = 2025 - min(j, 2)
        rows.append([
            f"NIPA-{year}-{100 + j * 7}",
            material_name,
            f"{round(bid.quantity * (0.7 + j * 0.2)):g}",
            f"Rs. {format_inr(anchor_price * (0.8 + j * 0.2))}",
            str(year),
        ])
    flow = [
        Paragraph(f"Supplier Name : {bid.supplier}", _BODY),
        Paragraph("Supply History (previous awards)", _H2),
        _style_table(rows, col_widths=[40 * mm, 50 * mm, 30 * mm, 40 * mm, 17 * mm]),
    ]
    _doc(path, flow, "Supply History", f"{manifest.case_id} — {bid.supplier}")


def _qty_of(reqs):
    for r in reqs:
        if r.field == "quantity":
            return float(r.value), r.unit or "nos"
    return None


def _budget_of(reqs):
    for r in reqs:
        if r.field == "price":
            return float(r.value)
    return None


def _delivery_of(reqs):
    for r in reqs:
        if r.field == "delivery_days":
            return float(r.value)
    return None


def _first_cert(reqs):
    vals = _cert_values(reqs)
    return vals[0] if vals else "ISO 9001:2015"


def _cert_values(reqs):
    for r in reqs:
        if r.field == "certification":
            v = r.value
            return v if isinstance(v, list) else [v]
    return []


def _qty_spec(reqs):
    q = _qty_of(reqs)
    return f"{q[0]:g} {q[1]} (minimum acceptable)" if q else "as per tender"


def _price_spec(reqs):
    b = _budget_of(reqs)
    return f"Total value shall not exceed Rs. {format_inr(b)}" if b else "as per tender"


def _delivery_spec(reqs):
    d = _delivery_of(reqs)
    return f"within {d:g} days" if d else "as per tender"