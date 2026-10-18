"""Inventory-backed duplicate checking and controlled workflow events."""

from collections.abc import Callable
from uuid import UUID

from stockentry_guard.application.use_cases.check_duplicates import CheckDuplicates
from stockentry_guard.domain.duplicates import DuplicateResult
from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.domain.models import Classification, ProductCandidate
from stockentry_guard.ports.unit_of_work import UnitOfWork


def record_duplicate_result(
    uow: UnitOfWork,
    result: DuplicateResult,
    correlation_id: UUID,
    *,
    engine_version: str,
    threshold_version: str,
) -> None:
    uow.events.append(
        WorkflowEvent(
            EventType.DUPLICATE_CHECKED,
            correlation_id,
            payload={
                "classification": result.classification.value,
                "engine_version": engine_version,
                "threshold_version": threshold_version,
                "top_score": result.top_score,
            },
        )
    )
    if result.classification == Classification.LIKELY_DUPLICATE:
        assert result.matched_product_id is not None
        uow.events.append(
            WorkflowEvent(
                EventType.DUPLICATE_FOUND,
                correlation_id,
                product_id=result.matched_product_id,
                payload={
                    "matched_product_id": str(result.matched_product_id),
                    "score": result.top_score,
                    "reason_code": "LIKELY_SCORE",
                },
            )
        )


class CheckProductDuplicates:
    def __init__(
        self,
        checker: CheckDuplicates,
        uow_factory: Callable[[], UnitOfWork],
        *,
        engine_version: str,
        threshold_version: str,
    ) -> None:
        self.checker = checker
        self.uow_factory = uow_factory
        self.engine_version = engine_version
        self.threshold_version = threshold_version

    def execute(self, candidate: ProductCandidate, *, correlation_id: UUID) -> DuplicateResult:
        with self.uow_factory() as uow:
            result = self.checker.execute(candidate, uow.products.list_all())
            self.record(uow, result, correlation_id)
            uow.commit()
        return result

    def record(self, uow: UnitOfWork, result: DuplicateResult, correlation_id: UUID) -> None:
        record_duplicate_result(
            uow,
            result,
            correlation_id,
            engine_version=self.engine_version,
            threshold_version=self.threshold_version,
        )
