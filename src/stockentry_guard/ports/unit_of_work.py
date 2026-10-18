"""Atomic inventory/event writes independent of database implementation."""

from types import TracebackType
from typing import Protocol, Self

from stockentry_guard.ports.repositories import EventRepository, ProductRepository


class UnitOfWork(Protocol):
    @property
    def products(self) -> ProductRepository: ...

    @property
    def events(self) -> EventRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back uncommitted work and release resources, even on errors."""
        ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
