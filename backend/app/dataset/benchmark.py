"""Benchmark builder: case bank generation + file layout.

Produces (regenerable, deterministic):

* ``data/synthetic/materials.json``, ``data/synthetic/suppliers.json``,
  ``data/synthetic/meta.json``
* ``data/synthetic/cases/<case_id>/manifest.json`` + PDF documents
* ``data/benchmark/cases/<case_id>.json`` (ground truth + tasks)
* ``data/benchmark/index.json``

Nothing in this module reads the production decision engine.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from backend.app.dataset import __version__ as DATASET_VERSION
from backend.app.dataset.catalog import CATALOG, Material
from backend.app.dataset.documents import (
    render_certificate,
    render_history,
    render_policy,
    render_quote,
    render_rfq,
    render_spec,
)
from backend.app.dataset.manifests import CaseManifest, from_json
from backend.app.dataset.reference import public_seed_ids, write_market_seed, write_provenance
from backend.app.dataset.scenarios import build_manifest
from backend.app.dataset.suppliers import SupplierProfile, build_roster

#: template -> number of cases (>=50 total)
TEMPLATE_COUNTS: dict[str, int] = {
    "compliance": 12,
    "certificate_gauntlet": 10,
    "quantity_units": 9,
    "currency_fx": 8,
    "unverifiable": 6,
    "abstention": 5,
    "dual_compliant": 5,
}

CASE_DATE_ANCHOR = date(2025, 6, 15)
ROSTER_SEED = "supplier-v1"


def _case_date(variant: int) -> date:
    return CASE_DATE_ANCHOR + timedelta(days=(variant % 12) * 3)


def _pick_suppliers(roster: list[SupplierProfile], variant: int, count: int = 3) -> list[SupplierProfile]:
    n = len(roster)
    picked: list[SupplierProfile] = []
    step = 5
    for i in range(count):
        idx = (variant * step + i * 7) % n
        picked.append(roster[idx])
    return picked


def _material_for(variant: int, offset: int = 0) -> Material:
    return CATALOG[variant % len(CATALOG)]


def case_bank() -> list[dict]:
    """Return the ordered list of raw authoring specs (one per case)."""
    roster = build_roster(count=36, seed=ROSTER_SEED)
    specs: list[dict] = []
    variant = 0
    for template, count in TEMPLATE_COUNTS.items():
        for k in range(count):
            material = _material_for(k + variant, offset=k)
            suppliers = _pick_suppliers(roster, variant, count=3)
            specs.append({
                "template": template,
                "material": material,
                "variant": variant,
                "suppliers": suppliers,
                "seed": f"case-v1:{variant}",
                "case_date": _case_date(variant),
            })
            variant += 1
    return specs


def _doc_filenames(manifest: CaseManifest) -> dict[str, str]:
    names: dict[str, str] = {"rfq": "rfq.pdf", "spec": "spec.pdf", "policy": "policy.pdf"}
    for i, bid in enumerate(manifest.authoring_suppliers):
        slug = bid.supplier.replace(" ", "_").replace("-", "_").lower()
        names[f"quote_{i}"] = f"quote_{slug}.pdf"
        if bid.certs:
            names[f"certificates_{i}"] = f"certificates_{slug}.pdf"
        names[f"history_{i}"] = f"history_{slug}.pdf"
    return names


def _render_documents(case_dir: Path, manifest: CaseManifest) -> dict[str, str]:
    names = _doc_filenames(manifest)
    mat = _material_by_id(manifest.material_id)
    reqs = manifest.authoring_requirements
    grade = mat.grades[0]

    render_rfq(case_dir / names["rfq"], manifest, reqs, mat.name)
    render_spec(case_dir / names["spec"], manifest, reqs, mat.name, grade)
    render_policy(case_dir / names["policy"], manifest, manifest.policy, _budget(reqs))

    for i, bid in enumerate(manifest.authoring_suppliers):
        slug = bid.supplier.replace(" ", "_").replace("-", "_").lower()
        render_quote(case_dir / names[f"quote_{i}"], manifest, bid)
        if bid.certs:
            for cert in bid.certs:
                render_certificate(case_dir / names[f"certificates_{i}"], manifest, bid, cert)
        render_history(case_dir / names[f"history_{i}"], manifest, bid, mat.name)
    return names


def _material_by_id(material_id: str) -> Material:
    from backend.app.dataset.catalog import BY_ID
    return BY_ID[material_id]


def _budget(reqs) -> float | None:
    for r in reqs:
        if r.field == "price":
            return float(r.value)
    return None


def generate_synthetic(root: Path) -> None:
    """Generate the full synthetic layer (manifests + PDFs) under ``root``."""
    synthetic = root / "synthetic"
    cases_dir = synthetic / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    roster = build_roster(count=36, seed=ROSTER_SEED)
    (synthetic / "suppliers.json").write_text(
        json.dumps({
            "synthetic": True,
            "disclaimer": "All suppliers in this roster are fictional. No entity or "
                          "transaction represents a real organisation.",
            "seed": ROSTER_SEED,
            "suppliers": [s.__dict__ for s in roster],
        }, indent=2), encoding="utf-8")

    (synthetic / "materials.json").write_text(
        json.dumps({
            "synthetic": True,
            "materials": [
                {"id": m.id, "name": m.name, "family": m.family, "unspsc": m.unspsc,
                 "grades": list(m.grades), "unit": m.unit, "base_price_inr": m.base_price_inr}
                for m in CATALOG
            ],
        }, indent=2), encoding="utf-8")

    manifests: list[CaseManifest] = []
    for spec in case_bank():
        manifest = build_manifest(spec)
        manifest.public_seeds = public_seed_ids()
        case_dir = cases_dir / f"bench-{spec['variant']:03d}"
        case_dir.mkdir(parents=True, exist_ok=True)
        manifest.documents = _render_documents(case_dir, manifest)
        (case_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        manifests.append(manifest)

    (synthetic / "meta.json").write_text(json.dumps({
        "dataset": "procurement-benchmark",
        "cohort": "synthetic-v1",
        "version": DATASET_VERSION,
        "synthetic": True,
        "generated_at": _stable_generated_at(),
        "case_count": len(manifests),
        "case_ids": [m.case_id for m in manifests],
        "templates": TEMPLATE_COUNTS,
    }, indent=2), encoding="utf-8")

    write_provenance()
    write_market_seed(root / "processed" / "reference" / "market_prices.json")


def _stable_generated_at() -> str:
    return "reproducible-from-seed (see scripts/generate_dataset.py)"


def build_benchmark(root: Path) -> None:
    """Assemble ``data/benchmark`` from ``data/synthetic`` manifests."""
    synthetic = root / "synthetic"
    bench = root / "benchmark"
    bench_cases = bench / "cases"
    bench_cases.mkdir(parents=True, exist_ok=True)

    manifest_files = sorted((synthetic / "cases").glob("*/manifest.json"))
    index: list[dict] = []
    for mf in manifest_files:
        manifest = from_json(mf.read_text(encoding="utf-8"))
        out = bench_cases / f"{manifest.case_id}.json"
        out.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        index.append({
            "case_id": manifest.case_id,
            "title": manifest.title,
            "template": manifest.template,
            "variant": manifest.variant,
            "tags": manifest.tags,
            "difficulty": _difficulty(manifest),
            "synthetic": manifest.synthetic,
            "ground_truth_file": f"cases/{manifest.case_id}.json",
        })

    (bench / "index.json").write_text(json.dumps({
        "dataset": "procurement-benchmark",
        "cohort": "synthetic-v1",
        "version": DATASET_VERSION,
        "case_count": len(index),
        "independence": "Ground truth is authored in backend/app/dataset/scenarios.py. "
                        "The production decision engine is never used to produce answers.",
        "public_seeds": public_seed_ids(),
        "cases": index,
    }, indent=2), encoding="utf-8")


def _difficulty(manifest: CaseManifest) -> str:
    if "abstention" in manifest.tags or "insufficient" in manifest.tags:
        return "conceptual"
    if len(manifest.ground_truth.expected_outcomes) >= 2 and all(
            o.get("status") != "PASS" for o in manifest.ground_truth.expected_outcomes.values()):
        return "hard"
    n_fail = sum(1 for o in manifest.ground_truth.expected_outcomes.values() if o.get("status") != "PASS")
    if n_fail >= 2:
        return "hard"
    return "medium"


def validate_benchmark(root: Path) -> list[str]:
    """Structural validation of the benchmark (no production engine involved)."""
    errors: list[str] = []
    bench = root / "benchmark"
    index_f = bench / "index.json"
    if not index_f.exists():
        return ["missing benchmark index"]
    index = json.loads(index_f.read_text(encoding="utf-8"))
    cases = index.get("cases", [])
    if len(cases) < 50:
        errors.append(f"only {len(cases)} cases (<50)")
    seen = set()
    for c in cases:
        cid = c["case_id"]
        if cid in seen:
            errors.append(f"duplicate case_id {cid}")
        seen.add(cid)
        mf = bench / "cases" / f"{cid}.json"
        if not mf.exists():
            errors.append(f"missing {cid}")
            continue
        manifest = from_json(mf.read_text(encoding="utf-8"))
        gt = manifest.ground_truth
        if not gt.expected_outcomes:
            errors.append(f"{cid}: no expected outcomes")
        for s, o in gt.expected_outcomes.items():
            if o.get("status") not in ("PASS", "FAIL", "UNVERIFIED"):
                errors.append(f"{cid}: bad status {o.get('status')}")
    return errors