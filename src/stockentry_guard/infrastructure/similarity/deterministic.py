"""Deterministic fuzzy comparison using only standard-library algorithms."""

from collections.abc import Sequence
from difflib import SequenceMatcher

from stockentry_guard.domain.models import Product, ProductCandidate, SignalScore, SimilarityMatch
from stockentry_guard.domain.normalization import ComparisonProduct, comparison_view


def text_similarity(left: str, right: str) -> float | None:
    if not left or not right:
        return None
    # Symmetric character score plus token containment for extended product names.
    character = (
        SequenceMatcher(None, left, right, autojunk=False).ratio()
        + SequenceMatcher(None, right, left, autojunk=False).ratio()
    ) / 2
    a, b = set(left.split()), set(right.split())
    containment = len(a & b) / min(len(a), len(b))
    return max(character, containment)


class DeterministicSimilarityEngine:
    """Scores are heuristics, never probabilities or authority to mutate inventory."""

    def __init__(self, *, minimum_name_similarity: float = 0.60) -> None:
        if not 0 <= minimum_name_similarity <= 1:
            raise ValueError("Minimum name similarity must be between zero and one")
        self._minimum_name_similarity = minimum_name_similarity

    def find_matches(
        self,
        candidate: ProductCandidate,
        inventory: Sequence[Product],
    ) -> Sequence[SimilarityMatch]:
        view = comparison_view(candidate)
        matches = [self._compare(view, comparison_view(product), product) for product in inventory]
        return tuple(sorted(matches, key=lambda item: (-item.score, str(item.product_id))))

    def _compare(
        self,
        left: ComparisonProduct,
        right: ComparisonProduct,
        product: Product,
    ) -> SimilarityMatch:
        name = text_similarity(left.name, right.name)
        brand = text_similarity(left.brand, right.brand)
        variant = text_similarity(left.variant, right.variant)
        category = text_similarity(left.category, right.category)
        size = float(left.size == right.size) if left.size and right.size else None
        barcode = float(left.barcode == right.barcode) if left.barcode and right.barcode else None
        sku = float(left.sku == right.sku) if left.sku and right.sku else None
        values = (
            ("name", name, 0.65),
            ("brand", brand, 0.10),
            ("variant", variant, 0.08),
            ("size", size, 0.15),
            ("category", category, 0.02),
        )
        denominator = sum(weight for _, value, weight in values if value is not None)
        score = (
            sum(value * weight for _, value, weight in values if value is not None) / denominator
            if denominator
            else 0.0
        )
        reasons: list[str] = []
        blockers = list(dict.fromkeys((*left.issues, *right.issues)))
        if not left.name and not left.barcode:
            blockers.append("MISSING_CANDIDATE_IDENTITY")
        if barcode == 1:
            score = max(score, 0.99)
            reasons.append("BARCODE_EXACT")
        if sku == 1:
            score = min(1.0, score + 0.02)
            reasons.append("SKU_EXACT")
        for conflict, code in (
            (size == 0, "SIZE_CONFLICT"),
            (
                bool(left.variant and right.variant and left.variant != right.variant),
                "VARIANT_CONFLICT",
            ),
            (barcode == 0, "BARCODE_CONFLICT"),
            (bool(left.brand and right.brand and left.brand != right.brand), "BRAND_CONFLICT"),
        ):
            if conflict:
                blockers.append(code)
                score = max(0.0, score - 0.15)
        # Missing evidence must never become an automatic name-only or SKU-only match.
        if barcode != 1 and (
            name is None
            or name < self._minimum_name_similarity
            or not (size == 1 or (brand == 1 and variant == 1))
        ):
            blockers.append("INSUFFICIENT_IDENTITY_EVIDENCE")
        signals = tuple(
            SignalScore(key, value, f"{key}: " + ("unknown" if value is None else f"{value:.3f}"))
            for key, value in (
                ("name", name),
                ("brand", brand),
                ("variant", variant),
                ("size", size),
                ("category", category),
                ("barcode", barcode),
                ("sku", sku),
            )
        )
        reasons.extend(blockers)
        reasons.extend(signal.explanation for signal in signals)
        return SimilarityMatch(
            product.id,
            score,
            tuple(reasons),
            signals,
            tuple(dict.fromkeys(blockers)),
        )
