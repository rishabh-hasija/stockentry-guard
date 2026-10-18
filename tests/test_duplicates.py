from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from stockentry_guard.application.use_cases.check_duplicates import CheckDuplicates
from stockentry_guard.bootstrap import build_duplicate_checker
from stockentry_guard.config import Settings
from stockentry_guard.domain.duplicates import DuplicateThresholds, classify_matches
from stockentry_guard.domain.models import (
    Classification,
    Product,
    ProductCandidate,
    SimilarityMatch,
)
from stockentry_guard.infrastructure.similarity.deterministic import DeterministicSimilarityEngine


def existing(name, **kwargs):
    return Product(id=uuid4(), name=name, normalized_name="unused comparison cache", **kwargs)


def check(candidate, inventory, thresholds=None):
    return CheckDuplicates(
        DeterministicSimilarityEngine(),
        thresholds or DuplicateThresholds(),
    ).execute(candidate, inventory)


@pytest.mark.parametrize(
    "stored,candidate,likely",
    [
        ("Coca-Cola Zero 330ml", "Coke Zero Sugar 330 ml", None),
        ("Coca-Cola Zero 330ml", "Coca-Cola Original 330ml", False),
        ("Coca-Cola Zero 330ml", "Coca-Cola Zero 500ml", False),
        ("Nutella 400g", "Nutella Hazelnut Spread 400 g", True),
        ("Red Bull Original 250ml", "Red Bull Sugarfree 250ml", False),
    ],
)
def test_required_retail_cases(stored, candidate, likely):
    result = check(ProductCandidate(name=candidate), [existing(stored)])
    if likely is True:
        assert result.classification == Classification.LIKELY_DUPLICATE
        assert result.top_score >= 0.85
    elif likely is False:
        assert result.classification != Classification.LIKELY_DUPLICATE
    else:
        assert result.classification in {
            Classification.NEEDS_REVIEW,
            Classification.LIKELY_DUPLICATE,
        }
        assert result.top_score >= 0.60


def test_equivalent_units_match():
    result = check(ProductCandidate(name="Cola 0.33 l"), [existing("Cola 330ml")])
    assert result.classification == Classification.LIKELY_DUPLICATE
    assert next(s.score for s in result.matches[0].signals if s.name == "size") == 1


def test_exact_barcode_is_strong_even_with_different_names():
    result = check(
        ProductCandidate(name="Short name", barcode="00123"),
        [
            existing("Completely different text", barcode="00123"),
        ],
    )
    assert result.classification == Classification.LIKELY_DUPLICATE
    assert result.top_score >= 0.99
    assert "BARCODE_EXACT" in result.matches[0].reasons


@pytest.mark.parametrize(
    "field,left,right,code",
    [
        ("barcode", "00123", "00999", "BARCODE_CONFLICT"),
        ("variant", "Original", "Sugarfree", "VARIANT_CONFLICT"),
        ("brand", "Brand A", "Brand B", "BRAND_CONFLICT"),
    ],
)
def test_conflicts_block_even_with_lower_likely_threshold(field, left, right, code):
    result = check(
        ProductCandidate(name="Drink 330ml", **{field: left}),
        [existing("Drink 330ml", **{field: right})],
        DuplicateThresholds(review=0.1, likely=0.2),
    )
    assert result.classification != Classification.LIKELY_DUPLICATE
    assert code in result.matches[0].blockers


def test_size_conflict_overrides_exact_barcode():
    result = check(
        ProductCandidate(name="Cola 330ml", barcode="001"),
        [
            existing("Cola 500ml", barcode="001"),
        ],
    )
    assert result.classification == Classification.NEEDS_REVIEW
    assert "SIZE_CONFLICT" in result.matches[0].blockers


def test_mass_and_volume_are_not_equal_sizes():
    result = check(ProductCandidate(name="Cream 100g"), [existing("Cream 100ml")])
    assert "SIZE_CONFLICT" in result.matches[0].blockers
    assert result.classification != Classification.LIKELY_DUPLICATE


def test_name_only_match_requires_review():
    result = check(ProductCandidate(name="Tea"), [existing("Tea")])
    assert result.classification == Classification.NEEDS_REVIEW
    assert "INSUFFICIENT_IDENTITY_EVIDENCE" in result.reason_codes
    assert next(s.score for s in result.matches[0].signals if s.name == "brand") is None


def test_no_inventory_is_new():
    result = check(ProductCandidate(name="Tea"), [])
    assert result.classification == Classification.NEW_PRODUCT
    assert result.matches == ()
    assert result.top_score == 0
    assert result.matched_product_id is None


def test_unrelated_product_is_new():
    result = check(
        ProductCandidate(name="Coffee", size_value=Decimal(400), size_unit="g"),
        [
            existing("Laundry detergent 2l"),
        ],
    )
    assert result.classification == Classification.NEW_PRODUCT


def test_sku_alone_is_not_identity():
    result = check(ProductCandidate(sku="0001"), [existing("Tea", sku="0001")])
    assert result.classification != Classification.LIKELY_DUPLICATE


def test_deterministic_order_and_ambiguous_top_matches():
    first = replace(existing("Nutella 400g"), id=UUID(int=1))
    second = replace(existing("Nutella 400g"), id=UUID(int=2))
    third = existing("Soap 100g")
    candidate = ProductCandidate(name="Nutella 400g")
    result = check(candidate, [second, third, first])
    assert result == check(candidate, [third, first, second])
    assert [m.product_id for m in result.matches[:2]] == [first.id, second.id]
    assert result.matched_product_id == first.id
    assert result.classification == Classification.NEEDS_REVIEW
    assert result.reason_codes == ("MULTIPLE_LIKELY_MATCHES",)


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.59, Classification.NEW_PRODUCT),
        (0.60, Classification.NEEDS_REVIEW),
        (0.849999, Classification.NEEDS_REVIEW),
        (0.85, Classification.LIKELY_DUPLICATE),
    ],
)
def test_threshold_boundaries(score, expected):
    result = classify_matches((SimilarityMatch(uuid4(), score, ()),), DuplicateThresholds())
    assert result.classification == expected


@pytest.mark.parametrize("review,likely", [(0.9, 0.8), (0.6, 0.6), (0, 0.8), (0.6, 1.1)])
def test_invalid_thresholds(review, likely):
    with pytest.raises(ValueError):
        DuplicateThresholds(review, likely)
    with pytest.raises(ValueError):
        Settings(
            _env_file=None, duplicate_review_threshold=review, duplicate_likely_threshold=likely
        )


def test_settings_thresholds_reach_use_case():
    checker = build_duplicate_checker(
        Settings(
            _env_file=None,
            duplicate_review_threshold=0.95,
            duplicate_likely_threshold=1.0,
        )
    )
    result = checker.execute(
        ProductCandidate(name="Short name", barcode="001"),
        [existing("Unrelated words", barcode="001")],
    )
    assert result.top_score == 0.99
    assert result.classification == Classification.NEEDS_REVIEW


@pytest.mark.parametrize("name", ["Cola 6 x 330ml", "Cola 330ml 500ml"])
def test_ambiguous_size_blocks_duplicate(name):
    result = check(ProductCandidate(name=name), [existing(name)])
    assert result.classification != Classification.LIKELY_DUPLICATE
    assert "AMBIGUOUS_SIZE" in result.matches[0].blockers


def test_empty_candidate_with_inventory_requires_review():
    result = check(ProductCandidate(), [existing("Tea")])
    assert result.classification == Classification.NEEDS_REVIEW
    assert result.reason_codes == ("MISSING_CANDIDATE_IDENTITY",)


def test_candidate_and_existing_are_not_mutated_by_matching():
    candidate = ProductCandidate(name=" Nutella 0.4 KG ", brand="NUTELLA")
    product = existing("Nutella 400g", brand="Nutella")
    before = replace(candidate), replace(product)
    check(candidate, [product])
    assert (candidate, product) == before


def test_unresolved_size_blocks_likely():
    result = check(
        ProductCandidate(name="Tea", size_value=Decimal(12), size_unit="oz"),
        [
            existing("Tea", barcode="001"),
        ],
    )
    assert result.classification != Classification.LIKELY_DUPLICATE
    assert "UNRESOLVED_SIZE" in result.matches[0].blockers


def test_signal_scores_are_bounded_and_unknown_is_not_agreement():
    result = check(
        ProductCandidate(name="Coke Zero Sugar 330ml"),
        [
            existing("Coca-Cola Zero 330ml"),
        ],
    )
    assert all(s.score is None or 0 <= s.score <= 1 for s in result.matches[0].signals)
    assert 0 <= result.top_score <= 1
    assert all(s.explanation for s in result.matches[0].signals)
