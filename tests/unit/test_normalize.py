"""Unit tests for normalization helpers used across the extraction/verification pipeline."""

from datetime import date

from backend.app.services.verifier import (
    convert_quantity,
    materials_match,
    normalize_certification,
    normalize_currency,
    normalize_material,
    normalize_text,
    normalize_unit,
    parse_date,
    parse_number,
    units_compatible,
)


class TestText:
    def test_collapse_and_lower(self):
        assert normalize_text("  SS 316 L  ") == "ss 316 l"
        assert normalize_text("ISO 9001:2015") == "iso 90012015"
        assert normalize_text("Nos.") == "nos"
        assert normalize_text("₹ 8,20,000") == "820000"


class TestMaterial:
    def test_grade_formatting(self):
        assert materials_match("SS 316L", "SS316L")
        assert materials_match("SS 316 L", "SS 316L")
        assert materials_match("SUS316L", "SS316L")
        assert materials_match("AISI 316L", "ss316l")

    def test_grade_is_strict(self):
        assert not materials_match("SS 316", "SS 316L")
        assert not materials_match("SS 304", "SS 316L")
        assert not materials_match("SS316L", "SS316LN")

    def test_uns_alloy_number(self):
        assert materials_match("1.4404", "SS316L")

    def test_empty_never_matches(self):
        assert not materials_match("", "SS 316L")
        assert not materials_match(None, "SS 316L")


class TestUnits:
    def test_aliases(self):
        assert normalize_unit("PCS") == "units"
        assert normalize_unit("Nos.") == "units"
        assert normalize_unit("Kgs") == "kg"
        assert normalize_unit("Metric Tons") == "mt"

    def test_conversion_same_family(self):
        assert convert_quantity(800, "kg", "mt") == 0.8

    def test_cross_family_rejected(self):
        assert convert_quantity(500, "metres", "units") is None

    def test_compatible(self):
        assert units_compatible("kg", "MT")
        assert not units_compatible("metres", "units")

    def test_unknown_units(self):
        assert normalize_unit("widgets") is None


class TestCurrency:
    def test_symbols_and_aliases(self):
        assert normalize_currency("₹") == "INR"
        assert normalize_currency("Rs") == "INR"
        assert normalize_currency("INR") == "INR"
        assert normalize_currency("€") == "EUR"
        assert normalize_currency("usd") == "USD"


class TestNumbers:
    def test_indian_formatting(self):
        assert parse_number("₹ 8,20,000") == 820000
        assert parse_number("820,000") == 820000
        assert parse_number("8.5 Lakh") == 850000
        assert parse_number("1.2 Crore") == 12000000

    def test_money_suffix(self):
        assert parse_number("€9,000") == 9000

    def test_garbage(self):
        assert parse_number("not-a-number") is None
        assert parse_number("") is None
        assert parse_number(None) is None

    def test_inline_quantity(self):
        assert parse_number(" 500 units ") == 500


class TestDates:
    def test_formats(self):
        assert parse_date("15/08/2025") == date(2025, 8, 15)
        assert parse_date("2025-08-15") == date(2025, 8, 15)
        assert parse_date("15 Aug 2025") == date(2025, 8, 15)
        assert parse_date("August 15, 2025") == date(2025, 8, 15)

    def test_invalid(self):
        assert parse_date("hello") is None
        assert parse_date(None) is None


class TestCertification:
    def test_normalization(self):
        assert normalize_certification("ISO 9001") == "ISO9001"
        assert normalize_certification("iso-9001:2015") == "ISO9001"
        assert normalize_certification(" ISO  9001 ") == "ISO9001"
        assert normalize_certification("ISO 14001:2015") == "ISO14001"