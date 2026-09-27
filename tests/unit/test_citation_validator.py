"""Unit tests for the deterministic citation validation layer.

Distinguishes a RETRIEVAL HIT (document found) from a VALID CITATION (the
passage actually supports the claim with correct case/document/page/supplier
metadata). All checks are deterministic; no LLM.

Scenarios covered (from the Phase 12 spec):

- VALID:  requirement material=SS316L, retrieved quote page 2, text "Material: SS316L"
- INVALID: requirement material=SS316L, retrieved quote page 4, text "Payment terms: Net 30"
- wrong case -> INVALID
- wrong page (both known) -> INVALID
- wrong supplier association -> INVALID
- text support with currency conversion (EUR price -> INR claim) -> VALID
- conflicting/missing evidence summaries via validate_critic_claims
"""

from __future__ import annotations

from backend.app.models.evidence import Evidence
from backend.app.services.citation_validator import (
    CitationCheck,
    validate_evidence_claim,
    validate_critic_claims,
    validate_retrieval_citation,
    _text_contains_claim,
)


def make_evidence(
    ident: str = "ev-1",
    case_id: str = "case-1",
    document_id: str = "doc-q1",
    document_name: str = "quote_acme.pdf",
    supplier_id: str | None = "sup-acme",
    doc_type: str = "quote",
    page: int = 2,
    section: str | None = "material",
    text: str = "Material: SS316L",
    field: str = "material",
    value=None,
) -> Evidence:
    return Evidence(
        id=ident,
        case_id=case_id,
        document_id=document_id,
        document_name=document_name,
        supplier_id=supplier_id,
        doc_type=doc_type,
        page=page,
        section=section,
        text=text,
        field=field,
        value={"v": value},
        confidence=1.0,
        source="extraction",
    )


class TestTextSupportsClaim:
    def test_material_substring(self):
        assert _text_contains_claim("Material: SS316L seamless pipe", "SS316L", "material")

    def test_material_normalized(self):
        assert _text_contains_claim("Material is SS 316L", "SS316L", "material")

    def test_number_direct(self):
        assert _text_contains_claim("Quantity required: 500 pieces", 500, "quantity")

    def test_number_converted_currency(self):
        # 3282 EUR at rate 95 -> 311790 INR (transformation-aware)
        assert _text_contains_claim("Total price: 3282 EUR", 311790, "price")

    def test_number_converted_units(self):
        # mt -> kg factor 1000: 2.5 mt = 2500 kg
        assert _text_contains_claim("Weight: 2.5 metric tons", 2500, "quantity")

    def test_payment_terms_does_not_support_material(self):
        assert not _text_contains_claim("Payment terms: Net 30", "SS316L", "material")


class TestCiteValidation:
    def test_valid_citation(self):
        # Spec example: requirement material=SS316L, quote page 2, text matches
        ev = make_evidence(text="Material: SS316L seamless pipe", page=2)
        check = validate_evidence_claim(ev, case_id="case-1", expected_value="SS316L",
                                        field_name="material", expected_page=2)
        assert check.status == "VALID"

    def test_wrong_passage_is_invalid(self):
        # Spec example: same requirement but text on the page is "Payment terms: Net 30"
        ev = make_evidence(text="Payment terms: Net 30", page=4, section="commercial",
                           field="price")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value="SS316L",
                                        field_name="material", expected_page=2)
        assert check.status == "INVALID"
        assert "does not contain claimed value" in check.reason

    def test_wrong_case_is_invalid(self):
        ev = make_evidence(case_id="case-other")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value="SS316L",
                                        field_name="material")
        assert check.status == "INVALID"
        assert "different case" in check.reason

    def test_wrong_page_is_invalid(self):
        ev = make_evidence(text="Material: SS316L", page=4)
        check = validate_evidence_claim(ev, case_id="case-1", expected_value="SS316L",
                                        field_name="material", expected_page=2)
        assert check.status == "INVALID"
        assert "page mismatch" in check.reason

    def test_wrong_supplier_is_invalid(self):
        ev = make_evidence(supplier_id="sup-other", text="Material: SS316L")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value="SS316L",
                                        field_name="material", expected_supplier_id="sup-acme")
        assert check.status == "INVALID"
        assert "supplier mismatch" in check.reason

    def test_wrong_doc_type_is_uncertain(self):
        # Price claim from a certificate doc: unusual but passage could still be valid
        ev = make_evidence(doc_type="certificate", text="Total price: 1000 EUR")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value=95000,
                                        field_name="price", expected_supplier_id="sup-acme")
        assert check.status == "UNCERTAIN"

    def test_transformed_currency_citation_valid(self):
        ev = make_evidence(field="price", text="Total price: 3282 EUR", section="commercial")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value=311790,
                                        field_name="price", expected_supplier_id="sup-acme")
        assert check.status == "VALID"

    def test_indian_lakh_number_format_valid(self):
        # "Rs. 10,96,650" is the Indian lakh/thousand format for 1096650
        ev = make_evidence(field="price", text="Total Price : Rs. 10,96,650", section="commercial")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value=1096650.0,
                                        field_name="price", expected_supplier_id="sup-acme")
        assert check.status == "VALID"

    def test_indian_lakh_notation_valid(self):
        # "Rs. 36.62 Lakh" = 3662000; claim may carry FP residue from multiplication
        ev = make_evidence(field="price", text="Total Price : Rs. 36.62 Lakh", section="commercial")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value=3661999.9999999995,
                                        field_name="price", expected_supplier_id="sup-acme")
        assert check.status == "VALID"

    def test_certification_with_version_valid(self):
        # claim "ISO9001" vs text "ISO 9001:2015" must match via normalization
        ev = make_evidence(field="certification", text="Certifications : ISO 9001:2015",
                           section="certification")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value="ISO9001",
                                        field_name="certification", expected_supplier_id="sup-acme")
        assert check.status == "VALID"

    def test_quantity_valid_in_quote_doc(self):
        ev = make_evidence(field="quantity", text="Quantity : 4879 m",
                           document_name="quote_acme.pdf", doc_type="quote")
        check = validate_evidence_claim(ev, case_id="case-1", expected_value=4879.0,
                                        field_name="quantity", expected_supplier_id="sup-acme")
        assert check.status == "VALID"

    def test_retrieval_citation_valid(self):
        check = validate_retrieval_citation(
            "Material: SS316L", "case-1", "quote", 2, None,
            expected_case_id="case-1", expected_value="SS316L", field_name="material",
        )
        assert check.status == "VALID"

    def test_retrieval_citation_wrong_passage(self):
        check = validate_retrieval_citation(
            "Payment terms: Net 30", "case-1", "quote", 4, None,
            expected_case_id="case-1", expected_value="SS316L", field_name="material",
            expected_page=2,
        )
        assert check.status == "INVALID"

    def test_retrieval_citation_cross_case(self):
        check = validate_retrieval_citation(
            "Material: SS316L", "case-2", "quote", 2, None,
            expected_case_id="case-1", expected_value="SS316L", field_name="material",
        )
        assert check.status == "INVALID"


class TestCriticClaimsIntegration:
    def test_supported_claim_has_valid_citations(self):
        ev = make_evidence(text="Material: SS316L", value="SS316L")
        decisions = [{
            "supplier": "Acme", "supplier_id": "sup-acme", "field": "material",
            "actual": "SS316L", "evidence_ids": ["ev-1"],
        }]
        result = validate_critic_claims(decisions, [ev], case_id="case-1")
        assert result["total"] == 1
        assert result["valid"] == 1
        assert result["invalid"] == 0
        assert result["coverage"] == 1.0

    def test_missing_evidence_row_is_uncertain(self):
        decisions = [{
            "supplier": "Acme", "supplier_id": "sup-acme", "field": "material",
            "actual": "SS316L", "evidence_ids": ["ev-missing"],
        }]
        result = validate_critic_claims(decisions, [], case_id="case-1")
        assert result["total"] == 1
        assert result["uncertain"] == 1

    def test_conflicting_evidence_flags_hard_failure(self):
        # Evidence text contradicts the claimed material -> INVALID citation
        ev = make_evidence(text="Material: AL6061", value="AL6061")
        decisions = [{
            "supplier": "Acme", "supplier_id": "sup-acme", "field": "material",
            "actual": "SS316L", "evidence_ids": ["ev-1"],
        }]
        result = validate_critic_claims(decisions, [ev], case_id="case-1")
        assert result["invalid"] == 1