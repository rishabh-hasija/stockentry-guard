"""Repositories stage writes in a shared transaction; they never commit."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from stockentry_guard.domain.events import StoredEvent, WorkflowEvent
from stockentry_guard.domain.models import Product


class ProductRepository(Protocol):
    def get(self, product_id: UUID) -> Product | None: ...

    def list_all(self) -> Sequence[Product]: ...

    def add(self, product: Product) -> None: ...

    def update(self, product: Product) -> Product:
        """Compare product.revision; return new revision or raise a concurrency error."""
        ...


class EventRepository(Protocol):
    def get(self, event_id: UUID) -> WorkflowEvent | None: ...

    def reserve(self, event: WorkflowEvent) -> None:
        """Stage and flush a unique event without committing; may raise EventReservationError."""
        ...

    def append(self, event: WorkflowEvent) -> None: ...

    def list_for_workflow(self, correlation_id: UUID) -> Sequence[WorkflowEvent]: ...

    def read_after(self, sequence: int = 0, *, limit: int = 100) -> Sequence[StoredEvent]: ...
