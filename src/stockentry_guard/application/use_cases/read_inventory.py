"""Read-only inventory and recent activity for a presentation layer."""

from collections.abc import Callable

from stockentry_guard.domain.events import StoredEvent
from stockentry_guard.domain.models import Product
from stockentry_guard.ports.unit_of_work import UnitOfWork


class ListInventory:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def execute(self) -> tuple[Product, ...]:
        with self._uow_factory() as uow:
            return tuple(uow.products.list_all())


class RecentActivity:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def execute(self, *, limit: int = 20) -> tuple[StoredEvent, ...]:
        with self._uow_factory() as uow:
            return tuple(uow.events.read_recent(limit=limit))
