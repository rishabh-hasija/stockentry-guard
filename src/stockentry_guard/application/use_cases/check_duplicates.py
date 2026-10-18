"""Duplicate checking without I/O, model calls, or inventory mutation."""

from collections.abc import Sequence

from stockentry_guard.domain.duplicates import (
    DuplicateResult,
    DuplicateThresholds,
    classify_matches,
)
from stockentry_guard.domain.models import Product, ProductCandidate
from stockentry_guard.ports.services import SimilarityEngine


class CheckDuplicates:
    def __init__(self, engine: SimilarityEngine, thresholds: DuplicateThresholds) -> None:
        self._engine = engine
        self._thresholds = thresholds

    def execute(self, candidate: ProductCandidate, inventory: Sequence[Product]) -> DuplicateResult:
        return classify_matches(
            tuple(self._engine.find_matches(candidate, inventory)), self._thresholds
        )

    def blocks_creation(self, result: DuplicateResult) -> bool:
        """Ambiguous multiple likely matches also require choosing an existing product."""
        return any(m.score >= self._thresholds.likely and not m.blockers for m in result.matches)
