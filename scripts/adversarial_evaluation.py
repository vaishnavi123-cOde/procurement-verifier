"""Phase 18 adversarial evaluation: 15 edge-case scenarios.

Creates synthetic PDFs with specific violation patterns, runs the LangGraph
pipeline over each, and reports whether the system correctly identifies the
violation or appropriately abstains when evidence is insufficient.

All output is real measured data from run_analysis. No fabricated results.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _pdf(title: str, lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 11)
    y = 760
    c.drawString(72, y, title)
    y -= 30
    for line in lines:
        if y < 72:
            c.showPage()
            y = 760
        c.drawString(72, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


def _run_scenario(name: str, rfq_lines: list[str], supplier_docs: dict[str, list[str]], db):
    from backend.app.database.engine import SessionLocal
    from backend.app.graphs.graph import run_analysis
    from backend.app.repositories import store
    from backend.app.services.ingestion import ingest_document

    case = store.create_case(db, name=name, description=f"adversarial: {name}")
    db.commit()
    db_case_id = case.id

    rfq_pdf = _pdf("RFQ - Request for Quotation", rfq_lines)
    ingest_document(db, db_case_id, "rfq.pdf", rfq_pdf, doc_type="rfq")

    for filename, lines in supplier_docs.items():
        doc_pdf = _pdf(f"Quotation - {filename}", lines)
        ingest_document(db, db_case_id, filename, doc_pdf, doc_type="quote")

    db.commit()

    start = time.monotonic()
    result = run_analysis(db, db_case_id, request_id=f"adv-{name}")
    duration_ms = int((time.monotonic() - start) * 1000)

    rec = result.get("recommendation", {})
    evaluations = result.get("supplier_evaluations", [])
    status = result.get("status", "")
    decision = result.get("decision_status", "")
    errors = result.get("errors", [])
    node_runs = result.get("node_runs", [])

    return {
        "scenario": name,
        "status": status,
        "decision_status": decision,
        "recommended_supplier": rec.get("recommended_supplier"),
        "recommendation_status": rec.get("status"),
        "confidence": rec.get("confidence"),
        "suppliers": [
            {"name": e.get("supplier_name"), "passed": e.get("passed"),
             "score": e.get("score"), "status": e.get("status"),
             "rejection_reasons": e.get("rejection_reasons", [])[:3],
             "unknowns": e.get("unknowns", [])[:3]}
            for e in evaluations
        ],
        "critic_status": result.get("critic_status"),
        "evidence_count": result.get("evidence_count", 0),
        "duration_ms": duration_ms,
        "errors": [e.get("error", "") for e in errors][:3],
        "node_runs": [{"node": n.get("node"), "status": n.get("status"),
                       "duration_ms": n.get("duration_ms", 0)}
                      for n in node_runs],
    }


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, tuple[str, list[str]], dict[str, list[str]]]] = [
    # 1 - wrong material
    ("wrong_material",
     ("SS 316L Seamless Pipe - RFQ",
      ["Requirement: Material must be SS 316L (AISI 316L).",
       "Quantity: 500 units.",
       "Delivery: 30 days.",
       "Budget: INR 500,000."]),
     {"quote_acme.pdf": [
         "Quotation for pipe supply.",
         "Material: SS 304 (ASTM A312).",
         "Quantity: 500 units.",
         "Price: INR 450,000.",
         "Delivery: 25 days."]}),

    # 2 - wrong quantity
    ("wrong_quantity",
     ("Industrial Pump Order - RFQ",
      ["Requirement: Quantity must be at least 200 units.",
       "Material: Cast Iron.",
       "Delivery: 45 days."]),
     {"quote_beta.pdf": [
         "Quotation for pump supply.",
         "Quantity: 80 units.",
         "Price: INR 180,000.",
         "Delivery: 40 days."]}),

    # 3 - wrong unit
    ("wrong_unit",
     ("Steel Plate Procurement - RFQ",
      ["Requirement: Quantity 5000 kg of A36 structural steel.",
       "Delivery: 21 days."]),
     {"quote_gamma.pdf": [
         "Quotation for steel plates.",
         "Quantity: 5000 pieces (individual plates).",
         "Price: INR 250,000.",
         "Delivery: 20 days."]}),

    # 4 - currency mismatch
    ("currency_mismatch",
     ("Electrical Components - RFQ",
      ["Requirement: Budget INR 200,000.",
       "Material: Copper wire, 2.5 sq mm.",
       "Delivery: 14 days."]),
     {"quote_delta.pdf": [
         "Quotation for copper wire.",
         "Price: USD 3,200.",
         "Quantity: 5000 metres.",
         "Delivery: 12 days."]}),

    # 5 - delivery violation
    ("delivery_violation",
     ("Urgent Spare Parts - RFQ",
      ["Requirement: Delivery within 7 calendar days.",
       "Item: Bearing 6205-2RS.",
       "Quantity: 100 units."]),
     {"quote_epsilon.pdf": [
         "Quotation for bearings.",
         "Delivery: 28 days (standard lead time).",
         "Price: INR 45,000.",
         "Quantity: 100 units."]}),

    # 6 - expired certification
    ("expired_certification",
     ("ISO-Certified Supplier Required - RFQ",
      ["Requirement: Supplier must hold valid ISO 9001:2015 certification.",
       "Material: Office furniture.",
       "Quantity: 50 sets."]),
     {"quote_zeta.pdf": [
         "Quotation for office desks and chairs.",
         "ISO 9001:2015 certification: valid until December 2022 (expired).",
         "Price: INR 750,000.",
         "Delivery: 21 days."]}),

    # 7 - missing certification
    ("missing_certification",
     ("Aviation-Grade Parts - RFQ",
      ["Requirement: AS9100D aerospace quality certification mandatory.",
       "Item: Titanium fasteners.",
       "Quantity: 2000 units."]),
     {"quote_eta.pdf": [
         "Quotation for titanium fasteners.",
         "No AS9100D certification.",
         "Price: INR 1,200,000.",
         "Delivery: 30 days."]}),

    # 8 - contradictory evidence
    ("contradictory_evidence",
     ("Raw Material Sourcing - RFQ",
      ["Requirement: Price for SS 316L bar stock must be below INR 800/kg.",
       "Quantity: 10,000 kg.",
       "Delivery: 14 days."]),
     {"quote_theta_a.pdf": [
         "Quotation from Steelcorp.",
         "Price: INR 750 per kg.",
         "Delivery: 12 days.",
         "Quantity: 10,000 kg."],
      "quote_theta_b.pdf": [
         "Updated quotation from Steelcorp.",
         "Price: INR 950 per kg (market adjustment).",
         "Delivery: 12 days.",
         "Quantity: 10,000 kg."]}),

    # 9 - missing evidence
    ("missing_evidence",
     ("Service Contract - RFQ",
      ["Requirement: Annual maintenance cost must be below INR 100,000.",
       "Requirement: 24/7 support availability.",
       "Service: HVAC maintenance contract."]),
     {"quote_iota.pdf": [
         "Quotation for HVAC maintenance.",
         "Annual price: INR 85,000.",
         "Delivery: N/A (service)."]}),
    # Note: no document mentions 24/7 support availability

    # 10 - unsupported supplier claim
    ("unsupported_claim",
     ("IT Infrastructure - RFQ",
      ["Requirement: Server warranty minimum 3 years.",
       "Item: Rack server, 64 GB RAM."]),
     {"quote_kappa.pdf": [
         "Quotation for rack server.",
         "Warranty: 5 years (verbal guarantee, no documentation).",
         "Price: INR 450,000.",
         "RAM: 64 GB."]}),

    # 11 - multiple compliant suppliers
    ("multiple_compliant",
     ("Bearing Procurement - RFQ",
      ["Requirement: Bearing type 6308, deep groove ball bearing.",
       "Quantity: 500 units.",
       "Price below INR 2,000 each.",
       "Delivery: 14 days."]),
     {"quote_lambda.pdf": [
         "Quotation from BearingTech.",
         "Bearing: 6308 deep groove ball bearing.",
         "Quantity: 500 units.",
         "Price: INR 1,800 each.",
         "Delivery: 10 days."],
      "quote_mu.pdf": [
         "Quotation from BearingWorld.",
         "Bearing: 6308 deep groove ball bearing.",
         "Quantity: 500 units.",
         "Price: INR 1,900 each.",
         "Delivery: 12 days."]}),

    # 12 - no compliant suppliers
    ("no_compliant",
     ("Precision Instrument - RFQ",
      ["Requirement: Instrument accuracy must be 0.01% or better.",
       "Requirement: NABL calibration certificate.",
       "Item: Digital micrometer."]),
     {"quote_nu.pdf": [
         "Quotation from ToolCo.",
         "Accuracy: 0.05%.",
         "No NABL calibration.",
         "Price: INR 25,000."],
      "quote_xi.pdf": [
         "Quotation from PrecisionParts.",
         "Accuracy: 0.1%.",
         "No NABL calibration.",
         "Price: INR 18,000."]}),

    # 13 - ambiguous requirement
    ("ambiguous_requirement",
     ("Office Supplies - RFQ",
      ["Requirement: High-quality paper for official use.",
       "Requirement: A4 size.",
       "Quantity: 100 reams.",
       "Budget: INR 50,000."]),
     {"quote_omicron.pdf": [
         "Quotation for A4 paper.",
         "Quality: Premium grade, 80 GSM.",
         "Quantity: 100 reams.",
         "Price: INR 42,000.",
         "Brand: PremiumPapers."]}),

    # 14 - historically good supplier now non-compliant
    ("historical_good_now_bad",
     ("Chemical Reagent Supply - RFQ",
      ["Requirement: Reagent purity must be 99.5% or above.",
       "Item: Sodium chloride, analytical grade.",
       "Quantity: 200 bottles."]),
     {"quote_pi.pdf": [
         "Quotation from ChemSuppliers Inc.",
         "Reagent: Sodium chloride, analytical grade.",
         "Purity: 98.0% (below required 99.5%).",
         "Quantity: 200 bottles.",
         "Price: INR 65,000."]}),

    # 15 - historically poor supplier now compliant
    ("historical_bad_now_good",
     ("CNC Machine Tooling - RFQ",
      ["Requirement: Tool hardness must be HRC 62 or above.",
       "Item: Carbide end mill, 10mm.",
       "Quantity: 50 units.",
       "Delivery: 10 days."]),
     {"quote_rho.pdf": [
         "Quotation from ToolMasters.",
         "Item: Carbide end mill, 10mm.",
         "Hardness: HRC 65.",
         "Quantity: 50 units.",
         "Price: INR 85,000.",
         "Delivery: 8 days."]}),
]


def main():
    from backend.app.database.engine import SessionLocal, init_db

    init_db()
    results = []

    with SessionLocal() as db:
        for name, (rfq_title, rfq_lines), docs in SCENARIOS:
            try:
                r = _run_scenario(name, [rfq_title, *rfq_lines], docs, db)
                results.append(r)
            except Exception as exc:
                results.append({"scenario": name, "error": str(exc)})
            db.rollback()  # fresh case per scenario

    # summary
    print("\n=== ADVERSARIAL EVALUATION ===\n")
    for r in results:
        sups = r.get("suppliers", [])
        sups_str = ", ".join(
            f"{s['name']}: {s['status']} (score={s.get('score')})"
            for s in sups
        ) if sups else "none"
        print(f"[{r['scenario']}] status={r.get('decision_status')} "
              f"recommend={r.get('recommended_supplier')} "
              f"confidence={r.get('confidence')} "
              f"evidence={r.get('evidence_count')} "
              f"suppliers=[{sups_str}] "
              f"critic={r.get('critic_status')} "
              f"duration={r.get('duration_ms')}ms")
        if r.get("errors"):
            for e in r["errors"]:
                print(f"  ERROR: {e}")

    report_path = ROOT / "data" / "evaluation_runs" / "adversarial_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nreport written: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
