"""Explicit atomic inventory import and read-only snapshot export."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from stockentry_guard.application.review import validate_reviewed
from stockentry_guard.application.workflow_errors import ApprovalRequiredError
from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.domain.models import Product
from stockentry_guard.domain.normalization import normalize_text
from stockentry_guard.ports.services import InventoryExporter, InventoryImporter
from stockentry_guard.ports.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class ImportResult:
    correlation_id: UUID
    product_ids: tuple[UUID, ...]
    accepted_count: int


class ImportInventory:
    def __init__(
        self,
        importer: InventoryImporter,
        uow_factory: Callable[[], UnitOfWork],
    ) -> None:
        self._importer = importer
        self._uow_factory = uow_factory

    def execute(self, content: bytes, *, correlation_id: UUID, approved: bool) -> ImportResult:
        if approved is not True:
            raise ApprovalRequiredError("Import requires explicit approval")
        candidates = self._importer.parse(content)
        # Materialize and validate everything before opening a write transaction.
        reviewed = [validate_reviewed(candidate) for candidate in candidates]
        products = tuple(
            Product(id=uuid4(), normalized_name=normalize_text(item.name), **item.model_dump())
            for item in reviewed
        )
        with self._uow_factory() as uow:
            for product in products:
                uow.products.add(product)
            uow.events.append(
                WorkflowEvent(
                    EventType.INVENTORY_IMPORTED,
                    correlation_id,
                    payload={
                        "accepted_count": len(products),
                        "source_fingerprint": sha256(content).hexdigest(),
                    },
                )
            )
            uow.commit()
        return ImportResult(correlation_id, tuple(p.id for p in products), len(products))


class ExportInventory:
    def __init__(
        self,
        exporter: InventoryExporter,
        uow_factory: Callable[[], UnitOfWork],
    ) -> None:
        self._exporter = exporter
        self._uow_factory = uow_factory

    def execute(self) -> bytes:
        with self._uow_factory() as uow:
            products = tuple(uow.products.list_all())
        return self._exporter.serialize(products)
