from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from stockentry_guard.domain.models import Product, ProductCandidate
from stockentry_guard.domain.normalization import (
    ComparisonSize,
    comparison_view,
    normalize_size,
    normalize_text,
    normalize_unit,
    normalize_variant,
    parse_size,
)


@pytest.mark.parametrize("value", [" Coca-Cola  ", "COCA COLA", "Coca—Cola", "Coca   Cola"])
def test_text_comparison(value):
    assert normalize_text(value) == "coca cola"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("330 ML", ComparisonSize(Decimal(330), "ml")),
        ("0.33 l", ComparisonSize(Decimal(330), "ml")),
        ("0,33 litres", ComparisonSize(Decimal(330), "ml")),
        ("0.4 kilograms", ComparisonSize(Decimal(400), "g")),
        ("400 grammes", ComparisonSize(Decimal(400), "g")),
    ],
)
def test_measurement_parsing(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize(
    "unit,expected",
    [
        ("milliliters", "ml"),
        ("LITRES", "l"),
        ("grams", "g"),
        ("kilogrammes", "kg"),
        ("oz", None),
        (None, None),
    ],
)
def test_common_units(unit, expected):
    assert normalize_unit(unit) == expected


@pytest.mark.parametrize("text", ["330", "0 ml", "-1 ml", "NaN l", "3 oz", "6 x 330 ml"])
def test_invalid_or_unsupported_size(text):
    assert parse_size(text) is None


def test_decimal_conversion_is_exact():
    value = Decimal("0.123456789012345678901234567890")
    assert normalize_size(value, "l").value == Decimal("123.456789012345678901234567890")


def test_variants_normalize_without_changing_meaning():
    assert normalize_variant(" ZERO-SUGAR ") == "zero"
    assert normalize_variant("Sugar Free") == "sugarfree"
    assert normalize_variant("City Edition") == "city edition"
    assert normalize_variant("Original") != normalize_variant("Zero")


def test_original_objects_remain_unchanged():
    candidate = ProductCandidate(
        name="  Nutella 400 g ",
        brand="NUTELLA",
        variant="Original",
        size_value=Decimal("0.4"),
        size_unit="kg",
        barcode="0001",
        sku=" A-01 ",
    )
    existing = Product(
        id=uuid4(),
        name=candidate.name,
        normalized_name="keep reviewed value",
        brand=candidate.brand,
        size_value=candidate.size_value,
        size_unit=candidate.size_unit,
    )
    before = replace(candidate), replace(existing)
    view = comparison_view(candidate)
    comparison_view(existing)
    assert (candidate, existing) == before
    assert view.name == "nutella"
    assert view.size == ComparisonSize(Decimal(400), "g")
    assert view.barcode == "0001"
    assert view.sku == " A-01 "


def test_contradictory_structured_and_visible_size_is_flagged():
    view = comparison_view(
        ProductCandidate(
            name="Cola 500ml",
            size_value=Decimal(330),
            size_unit="ml",
        )
    )
    assert "SIZE_EVIDENCE_CONFLICT" in view.issues


@pytest.mark.parametrize("name", ["Cola 6 x 330ml", "Cola 330ml 500ml"])
def test_pack_or_multiple_size_mentions_require_review(name):
    assert "AMBIGUOUS_SIZE" in comparison_view(ProductCandidate(name=name)).issues
