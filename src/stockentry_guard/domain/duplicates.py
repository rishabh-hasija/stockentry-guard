"""Framework-free classification policy for heuristic match evidence."""

from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from stockentry_guard.domain.models import Classification, SimilarityMatch


@dataclass(frozen=True, slots=True)
class DuplicateThresholds:
    review: float = 0.60
    likely: float = 0.85

    def __post_init__(self) -> None:
        if not (isfinite(self.review) and isfinite(self.likely)):
            raise ValueError("Thresholds must be finite")
        if not 0 < self.review < self.likely <= 1:
            raise ValueError("Require 0 < review < likely <= 1")


@dataclass(frozen=True, slots=True)
class DuplicateResult:
    classification: Classification
    matches: tuple[SimilarityMatch, ...]
    top_score: float
    matched_product_id: UUID | None
    reason_codes: tuple[str, ...]


def classify_matches(
    matches: tuple[SimilarityMatch, ...],
    thresholds: DuplicateThresholds,
) -> DuplicateResult:
    ranked = tuple(sorted(matches, key=lambda item: (-item.score, str(item.product_id))))
    if not ranked:
        return DuplicateResult(Classification.NEW_PRODUCT, (), 0.0, None, ("NO_INVENTORY",))
    top = ranked[0]
    reasons: tuple[str, ...]
    if "MISSING_CANDIDATE_IDENTITY" in top.blockers:
        classification = Classification.NEEDS_REVIEW
        reasons = ("MISSING_CANDIDATE_IDENTITY",)
    elif top.score < thresholds.review:
        classification = Classification.NEW_PRODUCT
        reasons = ("BELOW_REVIEW_THRESHOLD",)
    elif top.score < thresholds.likely or top.blockers:
        classification = Classification.NEEDS_REVIEW
        reasons = top.blockers or ("REVIEW_SCORE",)
    else:
        classification = Classification.LIKELY_DUPLICATE
        reasons = ("LIKELY_SCORE",)
    # Do not choose automatically between several plausible catalog records.
    if classification == Classification.LIKELY_DUPLICATE and any(
        match.score >= thresholds.likely and not match.blockers for match in ranked[1:]
    ):
        classification = Classification.NEEDS_REVIEW
        reasons = ("MULTIPLE_LIKELY_MATCHES",)
    return DuplicateResult(classification, ranked, top.score, top.product_id, reasons)
