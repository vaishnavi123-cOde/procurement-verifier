"""Unit tests for the deterministic verification engine.

These encode the hard invariants of the product:

- mandatory mismatches always FAIL (never "close enough")
- a cheaper supplier never wins on price alone
- missing evidence -> UNVERIFIED (abstention), never a PASS
- all arithmetic is deterministic
"""

from datetime import date

import pytest

from backend.app.services.verifier import (
    SupplierBid,
    CertificationInfo,
    CheckStatus,
    RequirementSpec,
    VerifierInput,
    evaluate_supplier,
)


def make_bid(**kwargs):
    defaults = dict(
        supplier_name="Supplier A",
        material="SS 316L",
        quantity=500,
        quantity_unit="units",
        price=820000,
        currency="INR",
        delivery_days=25,
        certifications=[CertificationInfo(name="ISO 9001", expiry_date=date(2033, 1, 1))],
    )
    defaults.update(kwargs)
    return SupplierBid(**defaults)


def make_requirements(**kwargs):
    defaults = [
        RequirementSpec(field="material", operator="eq", value="SS 316L", mandatory=True),
        RequirementSpec(field="quantity", operator="gte", value=500, unit="units", mandatory=True),
        RequirementSpec(field="price", operator="lte", value=900000, currency="INR", mandatory=True),
        RequirementSpec(field="delivery_days", operator="lte", value=30, mandatory=True),
        RequirementSpec(field="certification", value="ISO 9001", mandatory=True),
    ]
    custom = kwargs.pop("custom", None)
    if custom:
        custom_fields = {r.field for r in custom}
        defaults = [r for r in defaults if r.field not in custom_fields] + custom
    return defaults


class TestWorkingExample:
    """The canonical SS 316L example from the product brief."""

    def test_supplier_a_is_recommended(self):
        reqs = make_requirements()
        bid = make_bid(
            supplier_name="Supplier A",
            material="SS 316L",
            quantity=500,
            price=820000,
            delivery_days=25,
            certifications=[CertificationInfo(name="ISO 9001", expiry_date=date(2033, 1, 1))],
        )
        ev = evaluate_supplier(reqs, bid)
        assert ev.outcome.value == "PASS"
        assert ev.passed is True
        assert ev.score == 100
        assert ev.mandatory_passed is True

    def test_cheaper_material_mismatch_never_wins(self):
        """Supplier B is cheaper but material is SS 304 -> FAIL."""
        reqs = make_requirements()
        bid = make_bid(supplier_name="Supplier B", material="SS 304", price=760000)
        ev = evaluate_supplier(reqs, bid)
        assert ev.outcome.value == "FAIL"
        reasons = " ".join(ev.rejection_reasons).upper()
        assert "MATERIAL MISMATCH" in reasons
        # price itself still passes
        price_check = next(c for c in ev.checks if c.field == "price")
        assert price_check.status == CheckStatus.PASS

    def test_delivery_violation_rejected(self):
        reqs = make_requirements()
        bid = make_bid(supplier_name="Supplier C", price=850000, delivery_days=45)
        ev = evaluate_supplier(reqs, bid)
        assert ev.outcome.value == "FAIL"
        assert "DELIVERY VIOLATION" in " ".join(ev.rejection_reasons).upper()

    def test_price_violation_rejected(self):
        reqs = make_requirements()
        bid = make_bid(price=950000)
        ev = evaluate_supplier(reqs, bid)
        assert ev.outcome.value == "FAIL"
        assert "PRICE VIOLATION" in " ".join(ev.rejection_reasons).upper()

    def test_quantity_shortfall_rejected(self):
        reqs = make_requirements()
        bid = make_bid(quantity=450)
        ev = evaluate_supplier(reqs, bid)
        assert ev.outcome.value == "FAIL"
        assert "QUANTITY SHORTFALL" in " ".join(ev.rejection_reasons).upper()

    def test_missing_certification_rejected(self):
        reqs = make_requirements()
        bid = make_bid(certifications=[CertificationInfo(name="ISO 14001")])
        ev = evaluate_supplier(reqs, bid)
        assert ev.outcome.value == "FAIL"
        assert "MISSING CERTIFICATION" in " ".join(ev.rejection_reasons).upper()

    def test_multiple_failures_all_reported(self):
        reqs = make_requirements()
        bid = make_bid(material="SS 304", price=950000, delivery_days=45)
        ev = evaluate_supplier(reqs, bid)
        assert ev.outcome.value == "FAIL"
        assert len(ev.rejection_reasons) == 3

    def test_no_valid_supplier_detected(self):
        bad = make_bid(supplier_name="Supplier C", material="AL 6061", price=999999, delivery_days=60)
        ev = evaluate_supplier(make_requirements(), bad)
        assert not ev.passed


class TestMaterialStrictness:
    def test_316_is_not_316l(self):
        bid = make_bid(material="SS 316")
        ev = evaluate_supplier(make_requirements(), bid)
        assert not ev.passed
        assert any(c.field == "material" and c.status == CheckStatus.FAIL for c in ev.checks)

    def test_formatting_equivalence(self):
        """Same grade with different formatting still matches."""
        bid = make_bid(material="SS316L")
        assert evaluate_supplier(make_requirements(), bid).passed

    def test_sus_316l_alias(self):
        bid = make_bid(material="SUS316L")
        assert evaluate_supplier(make_requirements(), bid).passed


class TestCertificationValidity:
    def test_expired_certification_rejected(self):
        bid = make_bid(
            certifications=[
                CertificationInfo(
                    name="ISO 9001",
                    issue_date=date(2015, 1, 1),
                    expiry_date=date(2018, 12, 31),
                )
            ]
        )
        ev = evaluate_supplier(
            make_requirements(), bid, context=VerifierInput(case_date=date(2026, 1, 1))
        )
        # presence passes but validity fails
        assert any(
            c.status == CheckStatus.FAIL and "EXPIRED" in c.reason.upper()
            for c in ev.checks
        )
        assert not ev.passed

    def test_valid_until_future(self):
        bid = make_bid(
            certifications=[
                CertificationInfo(name="ISO 9001", expiry_date=date(2030, 12, 31))
            ]
        )
        ev = evaluate_supplier(
            make_requirements(), bid, context=VerifierInput(case_date=date(2026, 1, 1))
        )
        assert ev.passed

    def test_undated_certificate_is_warning_not_pass(self):
        bid = make_bid(certifications=[CertificationInfo(name="ISO 9001")])
        ev = evaluate_supplier(make_requirements(), bid)
        # Certificate present -> cert check passes
        assert any(
            c.field == "certification" and c.status == CheckStatus.PASS for c in ev.checks
        )
        # Validity not established -> warning (still passable at document level)
        assert any(c.field == "certification.validity" for c in ev.checks)


class TestAbstention:
    def test_missing_price_is_unverified_not_pass(self):
        reqs = make_requirements()
        bid = make_bid(price=None)
        ev = evaluate_supplier(reqs, bid)
        assert not ev.passed
        price_check = next(c for c in ev.checks if c.field == "price")
        assert price_check.status == CheckStatus.UNVERIFIED
        assert "INSUFFICIENT EVIDENCE" in price_check.reason.upper()
        assert ev.outcome.value in ("UNVERIFIED", "WARNING", "FAIL")

    def test_missing_delivery_is_unverified(self):
        reqs = make_requirements()
        bid = make_bid(delivery_days=None)
        ev = evaluate_supplier(reqs, bid)
        assert not ev.passed

    def test_missing_material_is_unverified(self):
        reqs = make_requirements()
        bid = make_bid(material=None)
        ev = evaluate_supplier(reqs, bid)
        assert not ev.passed
        material_check = next(c for c in ev.checks if c.field == "material")
        assert material_check.status == CheckStatus.UNVERIFIED


class TestUnitsAndCurrencies:
    def test_unit_conversion(self):
        reqs = make_requirements(custom=[
            RequirementSpec(field="quantity", operator="gte", value=0.5, unit="mt", mandatory=True),
        ])
        bid = make_bid(quantity=800, quantity_unit="kg")
        ev = evaluate_supplier(reqs, bid)
        assert ev.passed

    def test_unit_mismatch_warning(self):
        reqs = make_requirements(custom=[
            RequirementSpec(field="quantity", operator="gte", value=500, unit="units", mandatory=True),
        ])
        bid = make_bid(quantity=500, quantity_unit="metres")
        ev = evaluate_supplier(reqs, bid)
        assert not ev.passed
        assert any(c.field == "quantity" and c.status == CheckStatus.UNVERIFIED for c in ev.checks)

    def test_currency_mismatch_no_rate(self):
        reqs = make_requirements()
        bid = make_bid(price=820000, currency="EUR")
        ev = evaluate_supplier(reqs, bid, context=VerifierInput(default_currency="INR"))
        assert not ev.passed
        price_check = next(c for c in ev.checks if c.field == "price")
        assert price_check.status == CheckStatus.UNVERIFIED
        assert "CURRENCY MISMATCH" in price_check.reason.upper()

    def test_currency_mismatch_with_rate_converts(self):
        reqs = make_requirements()
        bid = make_bid(price=9000, currency="EUR")
        ev = evaluate_supplier(
            reqs, bid,
            context=VerifierInput(default_currency="INR", fx_rates={"EUR": 95.0}),
        )
        price_check = next(c for c in ev.checks if c.field == "price")
        assert price_check.status == CheckStatus.PASS  # 9000*95 = 855,000 <= 900,000


class TestTolerance:
    def test_quantity_tolerance_allows_small_shortfall(self):
        reqs = make_requirements(custom=[
            RequirementSpec(field="quantity", operator="gte", value=500, unit="units",
                            mandatory=True, tolerance=0.02),
        ])
        bid = make_bid(quantity=495)  # 1% shortfall, allowed
        ev = evaluate_supplier(reqs, bid)
        assert ev.passed

    def test_quantity_tolerance_rejects_large_shortfall(self):
        reqs = make_requirements(custom=[
            RequirementSpec(field="quantity", operator="gte", value=500, unit="units",
                            mandatory=True, tolerance=0.02),
        ])
        bid = make_bid(quantity=400)
        ev = evaluate_supplier(reqs, bid)
        assert not ev.passed


class TestRequiredField:
    def test_missing_required_field(self):
        reqs = make_requirements(custom=[
            RequirementSpec(field="required_field", value="payment_terms", mandatory=True),
        ])
        bid = make_bid(payment_terms=None)
        ev = evaluate_supplier(reqs, bid)
        assert not ev.passed
        assert "MISSING REQUIRED FIELD" in " ".join(ev.rejection_reasons).upper()

    def test_present_required_field(self):
        reqs = make_requirements(custom=[
            RequirementSpec(field="required_field", value="payment_terms", mandatory=True),
        ])
        bid = make_bid(payment_terms="30 days from invoice")
        ev = evaluate_supplier(reqs, bid)
        assert ev.passed


class TestDeliveryParsing:
    def test_weeks_converted_to_days(self):
        reqs = make_requirements()
        bid = make_bid(delivery_days=None, delivery_text="3 weeks")
        ev = evaluate_supplier(reqs, bid)
        # 21 days <= 30 -> passes
        assert next(c for c in ev.checks if c.field == "delivery_days").status == CheckStatus.PASS

    def test_violating_article_text(self):
        reqs = make_requirements()
        bid = make_bid(delivery_days=None, delivery_text="45 days")
        ev = evaluate_supplier(reqs, bid)
        assert next(c for c in ev.checks if c.field == "delivery_days").status == CheckStatus.FAIL