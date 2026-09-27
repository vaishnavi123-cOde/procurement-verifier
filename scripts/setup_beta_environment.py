"""Phase 23: build the beta test environment.

Loads a labelled cohort of synthetic benchmark cases into the working
(development) database so the app can be exercised by beta testers. Each case
is analysed once so evidence, per-case memory, and results are present in the
UI. Re-running is idempotent: existing DB cases and documents are reused.

The cohort intentionally covers the full decision space:

  bench-006  compliant  : one RECOMMENDED supplier, two rejected (price/mat.)
  bench-031  fx         : foreign-currency quote accepted, others rejected
  bench-039  abstain    : ALL suppliers UNVERIFIED (insufficient evidence)
  bench-045  no_valid   : ALL suppliers FAIL (no valid bid exists)
  bench-050  dual       : two PASS suppliers, one expired-certificate reject

Ground truth is read from data/benchmark/cases/<id>.json for labelling only;
it is never fed into the analysis pipeline.

Output: data/beta/beta_environment.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BETA_COHORT = ["bench-006", "bench-031", "bench-039", "bench-045", "bench-050"]

BETA_LABELS = {
    "bench-006": "compliant",
    "bench-031": "currency_fx",
    "bench-039": "abstention_insufficient",
    "bench-045": "abstention_no_valid",
    "bench-050": "dual_compliant",
}


def _ground_truth(case_id: str) -> dict:
    from backend.app.dataset.manifests import from_json

    manifest_file = REPO_ROOT / "data" / "benchmark" / "cases" / f"{case_id}.json"
    manifest = from_json(manifest_file.read_text(encoding="utf-8"))
    gt = manifest.ground_truth
    return {
        "title": manifest.title,
        "tags": manifest.tags,
        "expected_recommendation": gt.expected_recommendation,
        "expected_status": gt.expected_status,
        "expected_abstention": gt.expected_abstention,
        "expected_outcomes": {
            s: o.get("status") for s, o in gt.expected_outcomes.items()
        },
    }


def main() -> int:
    from backend.app.agents.graph import run_orchestrated_analysis
    from backend.app.database.engine import SessionLocal, init_db
    from backend.app.repositories import store
    from backend.app.services.case_loader import (
        find_or_create_case,
        ingest_missing_documents,
    )

    init_db()

    beta_file = REPO_ROOT / "data" / "beta" / "beta_environment.json"
    beta_file.parent.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    with SessionLocal() as session:
        for case_id in BETA_COHORT:
            gt = _ground_truth(case_id)
            case, case_dir = find_or_create_case(session, case_id)
            assert case_dir is not None, f"dataset directory missing for {case_id}"
            ingested = ingest_missing_documents(session, case.id, case_dir)

            meta = dict(case.metadata_json or {})
            meta["beta_program"] = True
            meta["beta_label"] = BETA_LABELS[case_id]
            case.metadata_json = meta
            session.commit()

            started = time.monotonic()
            outcome = run_orchestrated_analysis(session, case.id)
            duration_ms = int((time.monotonic() - started) * 1000)

            entry = {
                "case_id": case_id,
                "db_case_id": case.id,
                "label": BETA_LABELS[case_id],
                "documents_ingested_now": ingested,
                "duration_ms": duration_ms,
                "actual_status": (outcome.get("recommendation") or {}).get("status"),
                "actual_recommendation": (outcome.get("recommendation") or {}).get("recommended_supplier"),
                "actual_outcomes": {
                    e.get("supplier_name"): e.get("status")
                    for e in outcome.get("evaluations", [])
                },
                "ground_truth": gt,
                "synthetic_note": (
                    "DEMO/BETA: fully synthetic procurement case generated for "
                    "validation testing. Do not use values as real market data."
                ),
            }
            summary.append(entry)
            print(f"[{case_id}] label={entry['label']} actual_status={entry['actual_status']} "
                  f"rec={entry['actual_recommendation']} docs_now={ingested} "
                  f"duration_ms={duration_ms}")

    beta_file.write_text(json.dumps({
        "program": "phase23-beta",
        "cohort": BETA_COHORT,
        "all_synthetic": True,
        "cases": summary,
    }, indent=2), encoding="utf-8")
    print(f"\nbeta environment written: {beta_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())