"""Adversarial tests for the evidence-grounded critic (services.critic.audit).

Uses plain in-memory ``SimpleNamespace`` rows (no DB, no PDFs) so each scenario
maps 1:1 to a Phase 11 critic check:

1. Supplier claims ISO certification but certificate is absent  -> block
2. Quote: SS316L vs certificate: SS304                           -> CONTRADICTORY_EVIDENCE
3. Quote contains conflicting prices                             -> CONTRADICTORY_EVIDENCE
4. Delivery values conflict                                      -> CONTRADICTORY_EVIDENCE
5. Table quantity 500 vs text quantity 550                       -> EXTRACTION_CONFLICT
6. Evidence exists but does not support the claimed value        -> CITATION_MISMATCH
7. Recommendation contradicts deterministic verifier             -> INVALID_RECOMMENDATION + BLOCK
8. Fully supported recommendation                                -> PASS
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.app.services.critic import audit
from backend.app.services.verifier.normalize import materials_match


def _evidence(*, field, value, supplier_id="s1", document_name="acme_quote.pdf", eid="e1"):
    return SimpleNamespace(
        id=eid, case_id="c1", field=field, value={"v": value}, supplier_id=supplier_id,
        document_name=document_name, doc_type="quote", page=1, confidence=0.9,
    )


def _req(field, value, req_id="r1"):
    return SimpleNamespace(id=req_id, case_id="c1", field=field, value={"v": value})


def _check(requirement, field, status, actual, expected=None, reason=""):
    return {
        "requirement": requirement, "field": field, "status": status,
        "expected": expected, "actual": actual, "reason": reason,
    }


def _evaluation(supplier="Acme", status="PASS", checks=None, passed=True):
    return {"supplier_name": supplier, "passed": passed, "status": status,
            "checks": checks or []}


def _codes(verdict):
    return {i["code"] for i in verdict["issues"]}


def _blocking(verdict):
    return {i["code"] for i in verdict["blocking_issues"]}


# ---------------------------------------------------------------------------
# 1. Missing certificate
# ---------------------------------------------------------------------------

class TestMissingCertificate:
    def test_claims_iso_without_certificate_blocks(self):
        eval_row = _evaluation(checks=[
            _check("certification.ISO 9001", "certification", "PASS", "ISO 9001"),
            _check("material", "material", "PASS", "SS316L"),
        ])
        evidence = [_evidence(field="material", value="SS316L")]
        verdict = audit([eval_row], [], evidence, recommended_supplier="Acme",
                        supplier_name_to_id={"Acme": "s1"})

        # material is provable, certification is not -> at least one missing-evidence issue
        assert verdict["blocked"] is True
        assert verdict["status"] == "BLOCK"
        assert "MISSING_EVIDENCE" in _codes(verdict) or "UNSUPPORTED_CLAIM" in _codes(verdict)
        assert verdict["evidence_coverage"] < 1.0

    def test_unverified_supplier_can_force_abstain_path(self):
        # decision-level: a recommended supplier with an absent cert on a passing
        # claim is blocked (deterministic facts are untouched by the critic)
        eval_row = _evaluation(checks=[
            _check("certification.ISO 9001", "certification", "PASS", "ISO 9001"),
        ])
        verdict = audit([eval_row], [], [], recommended_supplier="Acme",
                        supplier_name_to_id={"Acme": "s1"})
        assert verdict["blocked"] is True
        assert "UNSUPPORTED_CLAIM" in _codes(verdict)


# ---------------------------------------------------------------------------
# 2. Material contradiction  (quote SS316L vs certificate SS304)
# ---------------------------------------------------------------------------

class TestMaterialContradiction:
    def test_quote_vs_certificate_conflict_is_flagged(self):
        assert not materials_match("SS316L", "SS304")
        ev1 = _evidence(field="material", value="SS316L", document_name="acme_quote.pdf", eid="e1")
        ev2 = _evidence(field="material", value="SS304", document_name="acme_cert.pdf", eid="e2")
        verdict = audit([], [], [ev1, ev2])
        assert "CONTRADICTORY_EVIDENCE" in _codes(verdict)
        assert verdict["status"] == "BLOCK"


# ---------------------------------------------------------------------------
# 3-4. Conflicting prices / delivery values
# ---------------------------------------------------------------------------

class TestNumericContradictions:
    def test_conflicting_prices(self):
        ev1 = _evidence(field="price", value=100.0, eid="e1")
        ev2 = _evidence(field="price", value=120.0, eid="e2")
        verdict = audit([], [], [ev1, ev2])
        assert "CONTRADICTORY_EVIDENCE" in _codes(verdict)
        issue = next(i for i in verdict["issues"] if i["code"] == "CONTRADICTORY_EVIDENCE")
        assert "price" in issue["field"]
        assert issue["evidence_ids"] == ["e1", "e2"]

    def test_conflicting_delivery_values(self):
        ev1 = _evidence(field="delivery_days", value=20, eid="e1")
        ev2 = _evidence(field="delivery_days", value=45, document_name="acme_history.pdf", eid="e2")
        verdict = audit([], [], [ev1, ev2])
        assert "CONTRADICTORY_EVIDENCE" in _codes(verdict)

    def test_identical_values_are_not_a_contradiction(self):
        ev1 = _evidence(field="delivery_days", value=20, eid="e1")
        ev2 = _evidence(field="delivery_days", value=20, eid="e2")
        verdict = audit([], [], [ev1, ev2])
        assert "CONTRADICTORY_EVIDENCE" not in _codes(verdict)


# ---------------------------------------------------------------------------
# 5. Extraction conflict  (requirement table=500 vs text=550)
# ---------------------------------------------------------------------------

class TestExtractionConflict:
    def test_requirement_rows_disagree(self):
        r1 = _req("quantity", 500, req_id="r1")
        r2 = _req("quantity", 550, req_id="r2")
        verdict = audit([], [r1, r2], [])
        assert "EXTRACTION_CONFLICT" in _codes(verdict)
        assert verdict["status"] == "WARNING"  # warning severity, not blocking
        assert not verdict["blocked"]
        issue = next(i for i in verdict["issues"] if i["code"] == "EXTRACTION_CONFLICT")
        assert issue["severity"] == "warning"


# ---------------------------------------------------------------------------
# 6. Citation mismatch  (evidence exists but doesn't support the claim)
# ---------------------------------------------------------------------------

class TestCitationMismatch:
    def test_evidence_does_not_support_claimed_value(self):
        # claim: quantity 500 verified PASS; evidence says 550 -> contradiction in citation
        eval_row = _evaluation(checks=[
            _check("quantity", "quantity", "PASS", 500, expected=500),
        ])
        evidence = [_evidence(field="quantity", value=550, eid="e1")]
        verdict = audit([eval_row], [], evidence, recommended_supplier="Acme",
                        supplier_name_to_id={"Acme": "s1"})
        assert "CITATION_MISMATCH" in _codes(verdict)
        assert verdict["status"] == "BLOCK"
        assert verdict["evidence_coverage"] == 0.0
        assert len(verdict["citation_errors"]) == 1
        assert verdict["unsupported_decisions"][0]["evidence_ids"] == ["e1"]

    def test_matching_evidence_supports_claim(self):
        eval_row = _evaluation(checks=[
            _check("quantity", "quantity", "PASS", 500, expected=500),
        ])
        evidence = [_evidence(field="quantity", value=500, eid="e1")]
        verdict = audit([eval_row], [], evidence, recommended_supplier="Acme",
                        supplier_name_to_id={"Acme": "s1"})
        assert "CITATION_MISMATCH" not in _codes(verdict)
        assert verdict["blocked"] is False
        assert verdict["evidence_coverage"] == 1.0
        assert verdict["supported_decisions"][0]["supplier"] == "Acme"

    def test_material_claim_supported_by_normalized_evidence(self):
        eval_row = _evaluation(checks=[
            _check("material", "material", "PASS", "SS 316L"),
        ])
        evidence = [_evidence(field="material", value="SS316L", eid="e1")]
        verdict = audit([eval_row], [], evidence, recommended_supplier="Acme",
                        supplier_name_to_id={"Acme": "s1"})
        assert "CITATION_MISMATCH" not in _codes(verdict)
        assert verdict["evidence_coverage"] == 1.0


# ---------------------------------------------------------------------------
# 7. Invalid recommendation
# ---------------------------------------------------------------------------

class TestInvalidRecommendation:
    def test_recommendation_outside_passing_set_blocks(self):
        # deterministic verifier says Acme FAILs; recommendation still names Acme
        failed = _evaluation("Acme", status="FAIL", checks=[], passed=False)
        verdict = audit([failed], [], [], recommended_supplier="Acme")
        assert "INVALID_RECOMMENDATION" in _codes(verdict)
        assert verdict["status"] == "BLOCK"
        assert verdict["blocked"] is True


# ---------------------------------------------------------------------------
# 8. Fully supported recommendation
# ---------------------------------------------------------------------------

class TestFullySupported:
    def test_clean_case_passes(self):
        eval_row = _evaluation(checks=[
            _check("material", "material", "PASS", "SS316L"),
            _check("quantity", "quantity", "PASS", 500),
            _check("price", "price", "PASS", 1000),
            _check("delivery_days", "delivery_days", "PASS", 20),
            _check("certification.ISO 9001", "certification", "PASS", "ISO 9001"),
        ])
        evidence = [
            _evidence(field="material", value="SS316L", eid="e1"),
            _evidence(field="quantity", value=500, eid="e2"),
            _evidence(field="price", value=1000, eid="e3"),
            _evidence(field="delivery_days", value=20, eid="e4"),
            _evidence(field="certification", value=["ISO 9001"], eid="e5"),
        ]
        verdict = audit([eval_row], [], evidence, recommended_supplier="Acme",
                        supplier_name_to_id={"Acme": "s1"})
        assert verdict["status"] == "PASS"
        assert verdict["blocked"] is False
        assert verdict["evidence_coverage"] == 1.0
        assert verdict["issues"] == []
        assert len(verdict["supported_decisions"]) == 5
        assert verdict["unsupported_decisions"] == []

    def test_warning_when_issues_are_warning_only(self):
        r1 = _req("price", 1000, req_id="r1")
        r2 = _req("price", 1100, req_id="r2")
        verdict = audit([], [r1, r2], [])
        assert verdict["status"] == "WARNING"
        assert verdict["blocked"] is False
        # no decided claims -> coverage is trivially 1.0
        assert verdict["evidence_coverage"] == 1.0