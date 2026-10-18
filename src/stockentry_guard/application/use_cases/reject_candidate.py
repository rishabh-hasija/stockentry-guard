"""Explicit rejection records a fact without modifying inventory."""

from collections.abc import Callable
from uuid import UUID

from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.ports.unit_of_work import UnitOfWork


class RejectCandidate:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def execute(self, *, correlation_id: UUID, reason_code: str | None = None) -> WorkflowEvent:
        event = WorkflowEvent(
            EventType.PRODUCT_REJECTED,
            correlation_id,
            payload={"reason_code": reason_code},
        )
        with self._uow_factory() as uow:
            uow.events.append(event)
            uow.commit()
        return event
