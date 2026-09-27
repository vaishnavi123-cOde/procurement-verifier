"""Unit tests for the synthetic benchmark generator.

Invariants under test:

- the case bank is deterministic and has >= 50 authored cases
- every case manifest is internally consistent (no ``InconsistentCase``)
- ground truth is complete (outcomes, status, evidence fields, recommendation)
- authoring is independent of the production decision engine (no import leakage)
- the rendered PDF set matches the manifest's document map
- authored numbers reproduce the authored PASS/FAIL/UNVERIFIED outcomes
"""

from __future__ import annotations

from datetime import date

from backend.app.dataset.benchmark import case_bank
from backend.app.dataset.manifests import from_json
from backend.app.dataset.scenarios import (
    InconsistentCase,
    _expected_status,
    _norm_material,
    build_manifest,
)
from backend.app.dataset.suppliers import build_roster


def all_manifests():
    return [build_manifest(spec) for spec in case_bank()]


def spec_by_template(template: str):
    for s in case_bank():
        if s["template"] == template:
            return s
    raise AssertionError(f"no spec for template {template}")


class TestCaseBank:
    def test_count_at_least_50(self):
        specs = case_bank()
        assert len(specs) >= 50

    def test_case_ids_unique(self):
        ids = [build_manifest(s).case_id for s in case_bank()]
        assert len(ids) == len(set(ids))

    def test_variants_unique(self):
        variants = [s["variant"] for s in case_bank()]
        assert len(variants) == len(set(variants))

    def test_deterministic_serialization(self):
        spec = spec_by_template("compliance")
        a = build_manifest(spec).model_dump_json(indent=2)
        b = build_manifest(spec).model_dump_json(indent=2)
        assert a == b

    def test_template_coverage(self):
        templates = {s["template"] for s in case_bank()}
        assert templates == {
            "compliance", "certificate_gauntlet", "quantity_units",
            "currency_fx", "unverifiable", "abstention", "dual_compliant",
        }


class TestConsistency:
    def test_no_inconsistent_cases(self):
        for spec in case_bank():
            build_manifest(spec)  # raises InconsistentCase on authored bug

    def test_known_statuses_and_templates(self):
        for m in all_manifests():
            assert m.ground_truth.expected_abstention == (
                m.ground_truth.expected_status != "recommended"
            )
            if m.template == "compliance":
                assert m.ground_truth.expected_status == "recommended"
            if m.template == "abstention":
                assert m.ground_truth.expected_status == "no_valid"
                assert m.ground_truth.expected_recommendation is None
            if m.template == "unverifiable":
                assert m.ground_truth.expected_status == "insufficient"

    def test_all_bid_outcomes_valid(self):
        for m in all_manifests():
            for outcome in m.ground_truth.expected_outcomes.values():
                assert outcome["status"] in ("PASS", "FAIL", "UNVERIFIED")

    def test_every_case_has_three_suppliers(self):
        for m in all_manifests():
            assert len(m.authoring_suppliers) == 3

    def test_evidence_fields_aligned_with_facts(self):
        for m in all_manifests():
            for bid in m.authoring_suppliers:
                fields = m.ground_truth.expected_evidence_fields[bid.supplier]
                if bid.material:
                    assert "material" in fields
                if bid.certs:
                    assert "certification" in fields
                if bid.total_price is None:
                    assert "price" not in fields


class TestAuthoringReproducesOutcome:
    def test_material_mismatch_flag(self):
        m = spec_by_template("compliance")
        manifest = build_manifest(m)
        req = next(r for r in manifest.authoring_requirements if r.field == "material")
        for bid in manifest.authoring_suppliers:
            if "material_mismatch" in bid.reason_categories:
                assert _norm_material(bid.material) != _norm_material(str(req.value))
            elif bid.outcome == "PASS":
                assert _norm_material(bid.material) == _norm_material(str(req.value))


class TestPdfRendering:
    def test_render_single_case(self, tmp_path):
        from backend.app.dataset.catalog import BY_ID
        from backend.app.dataset.documents import (
            render_certificate,
            render_history,
            render_policy,
            render_quote,
            render_rfq,
            render_spec,
        )
        from backend.app.dataset.manifests import CaseManifest, RequirementDT

        spec = spec_by_template("quantity_units")
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        manifest = build_manifest(spec)
        reqs = manifest.authoring_requirements
        mat = BY_ID[manifest.material_id]

        render_rfq(case_dir / "rfq.pdf", manifest, reqs, mat.name)
        render_spec(case_dir / "spec.pdf", manifest, reqs, mat.name, mat.grades[0])
        render_policy(case_dir / "policy.pdf", manifest, manifest.policy, None)
        for i, bid in enumerate(manifest.authoring_suppliers):
            render_quote(case_dir / f"quote_{i}.pdf", manifest, bid)
            for j, cert in enumerate(bid.certs):
                render_certificate(case_dir / f"cert_{i}_{j}.pdf", manifest, bid, cert)
            render_history(case_dir / f"history_{i}.pdf", manifest, bid, mat.name)

        pdfs = sorted(p.name for p in case_dir.glob("*.pdf"))
        assert len(pdfs) >= 9
        for p in pdfs:
            data = (case_dir / p).read_bytes()
            assert data.startswith(b"%PDF")

    def test_manifest_roundtrip(self):
        m = build_manifest(spec_by_template("compliance"))
        restored = from_json(m.model_dump_json())
        assert restored.case_id == m.case_id
        assert restored.ground_truth.expected_status == m.ground_truth.expected_status


class TestIndependence:
    def test_dataset_never_imports_production_engine(self):
        import ast
        from pathlib import Path

        forbidden = ("backend.app.services.verifier", "backend.app.services.analysis")
        for f in (Path("backend/app/dataset")).rglob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(forbidden):
                    raise AssertionError(f"{f} imports {node.module}")
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.startswith(forbidden):
                            raise AssertionError(f"{f} imports {a.name}")


def test_expected_status_helper():
    from backend.app.dataset.manifests import BidTruth

    winner = BidTruth(supplier="A", outcome="PASS", material="SS 316L", quantity=1, quantity_unit="kg")
    loser = BidTruth(supplier="B", outcome="FAIL", material="SS 316L", quantity=1, quantity_unit="kg")
    rec, status = _expected_status("compliance", [winner, loser])
    assert rec == "A"
    assert status == "recommended"

    rec2, status2 = _expected_status("abstention", [loser])
    assert rec2 is None
    assert status2 == "no_valid"