"""Provider-neutral extraction, matching, and inventory interchange contracts."""

from collections.abc import Sequence
from typing import Protocol

from stockentry_guard.domain.models import Product, ProductCandidate, SimilarityMatch
from stockentry_guard.ports.extraction import ExtractionResult


class AIProductExtractor(Protocol):
    def extract(self, image: bytes, *, media_type: str) -> ExtractionResult:
        """Return unapproved candidate and metadata; failures raise ExtractionError."""
        ...


class SimilarityEngine(Protocol):
    def find_matches(
        self, candidate: ProductCandidate, inventory: Sequence[Product]
    ) -> Sequence[SimilarityMatch]: ...


class InventoryImporter(Protocol):
    """Decode and validate all input before any inventory mutation."""

    def parse(self, content: bytes) -> Sequence[ProductCandidate]: ...


class InventoryExporter(Protocol):
    def serialize(self, products: Sequence[Product]) -> bytes: ...
