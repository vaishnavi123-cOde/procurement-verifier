"""Benchmark runner over the LangGraph orchestration pipeline.

Same contract as ``scripts/evaluate.py`` (the deterministic baseline) but the
analysis step is executed through the LangGraph workflow
(``backend.app.graphs.graph.run_analysis``) instead of the classic
``run_orchestrated_analysis`` path.

Ground truth is read independently from ``data/benchmark/cases/<id>.json`` and
is never fed into the pipeline.

Usage:
    python scripts/evaluate_langgraph.py [--data DIR] [--limit N] [--cases a,b,c]

Exit code 0 = all cases passed; 2 = any case failed (matches evaluate.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the LangGraph benchmark over synthetic cases.")
    p.add_argument("--data", type=Path, default=None, help="data root (default: <repo>/data)")
    p.add_argument("--limit", type=int, default=None, help="only evaluate the first N cases")
    p.add_argument("--cases", type=str, default="", help="comma-separated case ids or a range a:b")
    return p.parse_args()


def _select_case_ids(args) -> list[str]:
    from backend.app.dataset.benchmark import case_bank
    from backend.app.dataset.scenarios import build_manifest

    all_ids = [build_manifest(s).case_id for s in case_bank()]
    selected = list(all_ids)
    if args.cases:
        tokens = [t.strip() for t in args.cases.split(",") if t.strip()]
        if any(":" in t for t in tokens):
            def idx(cid: str) -> int:
                return int(cid.rsplit("-", 1)[-1])

            bounds: list[int] = []
            for t in tokens:
                if ":" in t:
                    a, b = (int(x) for x in t.split(":", 1))
                    bounds += [a, b]
                else:
                    bounds += [int(t), int(t)]
            selected = []
            for a, b in zip(bounds[::2], bounds[1::2]):
                selected += [c for c in all_ids if a <= idx(c) <= b]
        else:
            selected = [c for c in all_ids if any(c.endswith(t) or t in c for t in tokens)]
    if args.limit:
        selected = selected[: args.limit]
    return selected


def main() -> int:
    args = _parse_args()
    data_root = (args.data or REPO_ROOT / "data").resolve()

    runs_dir = data_root / "evaluation_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    os.environ["DATABASE_URL"] = f"sqlite:///{(runs_dir / 'evaluation_langgraph.db').as_posix()}"
    os.environ["UPLOAD_DIR"] = str(runs_dir / "uploads_langgraph")
    os.environ["VECTOR_STORE_MODE"] = "memory"

    from backend.app.database.engine import SessionLocal, init_db
    from backend.app.dataset.manifests import from_json
    from backend.app.graphs.graph import graph_structure, run_analysis
    from backend.app.repositories import store

    init_db()

    case_ids = _select_case_ids(args)
    print(f"evaluating {len(case_ids)} cases via LangGraph")

    per_case: list[dict] = []
    agg = {
        "case": {"pass": 0, "total": 0},
        "recommendation": {"pass": 0, "total": 0},
        "status": {"pass": 0, "total": 0},
        "abstention": {"pass": 0, "total": 0},
        "bid_outcomes": {"match": 0, "total": 0},
        "evidence_fields": {"hit": 0, "total": 0},
        "duration_ms": [],
        "node_duration_ms": {},
        "coverage": [],
        "blocked": {"count": 0},
        "citation": {"checked": 0, "valid": 0, "uncertain": 0, "invalid": 0, "cases_with_checks": 0},
    }

    with SessionLocal() as session:
        run = store.create_evaluation_run(session, name="synthetic-langgraph", mode="langgraph")
        run_id = run.id
        session.commit()

        for cid in case_ids:
            started = time.monotonic()
            result = _run_one(session, run_analysis, from_json, data_root, cid, run_id)
            if result is None:
                print(f"  {cid}: ERROR (see above)")
                continue
            per_case.append(result)
            _accumulate(agg, result)

        store.finish_evaluation_run(
            session, run_id,
            total_cases=len(case_ids),
            passed_cases=agg["case"]["pass"],
            metrics=_metrics(agg),
        )
        session.commit()

    _print_report(per_case, _metrics(agg))
    report_path = runs_dir / "report_langgraph.json"
    report_path.write_text(json.dumps({
        "dataset": "procurement-benchmark",
        "cohort": "synthetic-v1-langgraph",
        "graph": graph_structure(),
        "cases": per_case,
        "metrics": _metrics(agg),
        "node_duration_ms": agg["node_duration_ms"],
    }, indent=2), encoding="utf-8")
    print(f"report written: {report_path}")
    return 0 if agg["case"]["total"] and agg["case"]["pass"] == agg["case"]["total"] else 2


def _run_one(session, run_analysis, from_json, data_root: Path, case_id: str, run_id: str):
    from backend.app.repositories import store

    manifest_file = data_root / "benchmark" / "cases" / f"{case_id}.json"
    manifest = from_json(manifest_file.read_text(encoding="utf-8"))
    gt = manifest.ground_truth

    try:
        outcome = run_analysis(session, case_id)
    except Exception as exc:  # noqa: BLE001 - evaluator must not crash on one case
        import traceback
        traceback.print_exc()
        return None

    actual = {
        "recommended_supplier": outcome["recommendation"].get("recommended_supplier"),
        "status": outcome["recommendation"].get("status"),
        "ranked_suppliers": outcome["recommendation"].get("ranked_suppliers", []),
        "bids": [
            {"supplier_name": e["supplier_name"], "status": e["status"]}
            for e in outcome["supplier_evaluations"]
        ],
    }

    expected = {
        "recommended_supplier": gt.expected_recommendation,
        "status": gt.expected_status,
        "bids": [
            {"supplier_name": s, "status": o.get("status")}
            for s, o in gt.expected_outcomes.items()
        ],
    }

    ev = _evaluate_case(gt, outcome, manifest)
    duration_ms = outcome.get("duration_ms") or 0
    record = store.add_evaluation_result(
        session, run_id=run_id, case_id=case_id, difficulty_tags=manifest.tags,
        expected=expected, actual=actual, metrics=ev["metrics"], passed=ev["passed"],
        duration_ms=duration_ms,
    )
    session.commit()
    return {
        "case_id": case_id,
        "title": manifest.title,
        "tags": manifest.tags,
        "expected": expected,
        "actual": actual,
        "passed": ev["passed"],
        "metrics": ev["metrics"],
        "duration_ms": duration_ms,
        "node_duration_ms": {r["node"]: r["duration_ms"] for r in outcome.get("node_runs", [])},
        "evidence_coverage": outcome.get("evidence_coverage", 1.0),
        "critic_blocked": outcome.get("critic_blocked", False),
        "decision_status": outcome.get("decision_status", ""),
        "citation_checks": outcome.get("citation_checks", []),
    }


def _evaluate_case(gt, outcome, manifest) -> dict:
    passed = True
    metrics: dict[str, object] = {}

    rec_expected = gt.expected_recommendation
    rec_actual = outcome["recommendation"].get("recommended_supplier")
    rec_hit = rec_expected == rec_actual
    metrics["recommendation"] = rec_hit
    passed = passed and rec_hit

    status_hit = gt.expected_status == outcome["recommendation"].get("status")
    metrics["status"] = status_hit
    passed = passed and status_hit

    abstain = outcome["recommendation"].get("recommended_supplier") is None
    metrics["abstention"] = abstain == gt.expected_abstention
    passed = passed and (abstain == gt.expected_abstention)

    actual_by_name = {e["supplier_name"].strip().lower(): e["status"] for e in outcome["supplier_evaluations"]}
    bid_ok = 0
    bid_total = 0
    detail: dict[str, dict] = {}
    for supplier, expected_outcome in gt.expected_outcomes.items():
        got = actual_by_name.get(supplier.strip().lower())
        bid_total += 1
        ok = got == expected_outcome.get("status")
        if ok:
            bid_ok += 1
        detail[supplier] = {"expected": expected_outcome.get("status"), "actual": got}
        passed = passed and ok
    metrics["bid_accuracy"] = bid_ok / bid_total if bid_total else 1.0
    metrics["bid_detail"] = detail

    ev_hit, ev_total, ev_detail = _evidence_recall(gt, outcome)
    metrics["evidence_recall"] = ev_hit / ev_total if ev_total else 1.0
    metrics["evidence_detail"] = ev_detail

    metrics["passed"] = passed
    return {"passed": passed, "metrics": metrics}


def _evidence_recall(gt, outcome):
    hit = total = 0
    detail: dict[str, dict] = {}
    actual_by_name = {e["supplier_name"].strip().lower(): e for e in outcome["supplier_evaluations"]}
    for supplier, fields in gt.expected_evidence_fields.items():
        ev = _actual_evidence_fields(outcome, actual_by_name, supplier)
        detail[supplier] = {"expected": sorted(fields), "actual": sorted(ev)}
        for f in fields:
            total += 1
            hit += 1 if f in ev else 0
    return hit, total, detail


def _actual_evidence_fields(outcome, actual_by_name, supplier):
    ev = set()
    for b in outcome["supplier_evaluations"]:
        if b["supplier_name"].strip().lower() == supplier.strip().lower():
            for c in b.get("checks", []):
                if c["status"] in ("PASS", "FAIL") and c.get("actual") is not None:
                    ev.add(c["field"])
    for r in (outcome["recommendation"] or {}).get("ranked_suppliers", []):
        if str(r.get("supplier_name", "")).strip().lower() == supplier.strip().lower():
            for c in r.get("checks", []):
                if c["status"] in ("PASS", "FAIL") and c.get("actual") is not None:
                    ev.add(c["field"])
    return ev


def _accumulate(agg: dict, result: dict) -> None:
    m = result["metrics"]
    agg["case"]["total"] += 1
    agg["case"]["pass"] += 1 if m["passed"] else 0
    for key in ("recommendation", "status", "abstention"):
        agg[key]["total"] += 1
        agg[key]["pass"] += 1 if m[key] else 0
    agg["bid_outcomes"]["total"] += m["bid_accuracy"] * len(m["bid_detail"])
    agg["bid_outcomes"]["match"] += round(m["bid_accuracy"] * len(m["bid_detail"]))
    agg["evidence_fields"]["total"] += sum(len(d["expected"]) for d in m["evidence_detail"].values())
    agg["evidence_fields"]["hit"] += sum(
        len(set(d["expected"]) & set(d["actual"])) for d in m["evidence_detail"].values()
    )
    agg["duration_ms"].append(result["duration_ms"])
    for node, duration in result.get("node_duration_ms", {}).items():
        slot = agg["node_duration_ms"].setdefault(node, {"total": 0, "count": 0})
        slot["total"] += duration
        slot["count"] += 1
    agg["coverage"].append(result.get("evidence_coverage", 1.0))
    if result.get("critic_blocked"):
        agg["blocked"]["count"] += 1
    checks = result.get("citation_checks") or []
    if checks:
        agg["citation"]["cases_with_checks"] += 1
    for c in checks:
        agg["citation"]["checked"] += 1
        agg["citation"][c.get("status", "").lower()] = (
            agg["citation"].get(c.get("status", "").lower(), 0) + 1
        )


def _metrics(agg: dict) -> dict:
    n = max(1, agg["case"]["total"])
    bid_total = max(1, agg["bid_outcomes"]["total"])
    ev_total = max(1, agg["evidence_fields"]["total"])
    cit_total = max(1, agg["citation"]["checked"])
    return {
        "case_accuracy": agg["case"]["pass"] / n,
        "recommendation_accuracy": agg["recommendation"]["pass"] / max(1, agg["recommendation"]["total"]),
        "status_accuracy": agg["status"]["pass"] / max(1, agg["status"]["total"]),
        "abstention_accuracy": agg["abstention"]["pass"] / max(1, agg["abstention"]["total"]),
        "bid_outcome_accuracy": agg["bid_outcomes"]["match"] / bid_total,
        "evidence_recall": agg["evidence_fields"]["hit"] / ev_total,
        "avg_duration_ms": round(sum(agg["duration_ms"]) / max(1, len(agg["duration_ms"])), 1),
        "avg_evidence_coverage": round(sum(agg["coverage"]) / max(1, len(agg["coverage"])), 4),
        "critic_blocked_cases": agg["blocked"]["count"],
        "citation_checks": agg["citation"]["checked"],
        "citation_cases_with_checks": agg["citation"]["cases_with_checks"],
        "citation_correctness": round(agg["citation"]["valid"] / cit_total, 4),
        "citation_uncertainty_rate": round(agg["citation"]["uncertain"] / cit_total, 4),
        "citation_mismatch_rate": round(agg["citation"]["invalid"] / cit_total, 4),
    }


def _print_report(per_case: list[dict], metrics: dict) -> None:
    total = len(per_case)
    passed_count = sum(1 for c in per_case if c["passed"])
    print(f"\n--- LangGraph benchmark report ({total} cases, {passed_count} passed) ---")
    for key, value in metrics.items():
        print(f"  {key}: {value}")
    print("\nfailures:")
    for c in per_case:
        if not c["passed"]:
            print(f"  {c['case_id']} [{','.join(c['tags'])}] "
                  f"expected={c['expected']['status']}/rec={c['expected']['recommended_supplier']} "
                  f"got={c['actual']['status']}/rec={c['actual']['recommended_supplier']}")


if __name__ == "__main__":
    raise SystemExit(main())