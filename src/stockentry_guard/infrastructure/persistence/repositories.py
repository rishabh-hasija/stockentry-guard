"""SQLAlchemy repositories sharing their caller's session and transaction."""

from collections.abc import Sequence
from dataclasses import asdict, replace
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stockentry_guard.application.event_payloads import validated_payload
from stockentry_guard.domain.errors import ConcurrentProductUpdateError, EventReservationError
from stockentry_guard.domain.events import EventType, StoredEvent, WorkflowEvent
from stockentry_guard.domain.models import Product, utc_now
from stockentry_guard.infrastructure.persistence.models import ProductRow, WorkflowEventRow


def product_from_row(row: ProductRow) -> Product:
    return Product(**{field: getattr(row, field) for field in Product.__dataclass_fields__})


def event_from_row(row: WorkflowEventRow) -> WorkflowEvent:
    return WorkflowEvent(
        event_type=EventType(row.event_type),
        correlation_id=row.correlation_id,
        product_id=row.product_id,
        payload=dict(row.payload),
        schema_version=row.schema_version,
        event_id=row.event_id,
        occurred_at=row.occurred_at,
    )


class SQLAlchemyProductRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, product_id: UUID) -> Product | None:
        row = self._session.get(ProductRow, product_id, populate_existing=True)
        return product_from_row(row) if row is not None else None

    def list_all(self) -> Sequence[Product]:
        rows = self._session.scalars(select(ProductRow).order_by(ProductRow.id)).all()
        return [product_from_row(row) for row in rows]

    def add(self, product: Product) -> None:
        if not isinstance(product, Product):
            raise TypeError("Only approved Product objects can be staged as inventory")
        if product.revision != 1:
            raise ValueError("New products must start at revision 1")
        self._session.add(ProductRow(**asdict(product)))

    def update(self, product: Product) -> Product:
        """Compare the expected revision; the returned revision is provisional until commit."""
        self._session.flush()
        updated = replace(
            product,
            revision=product.revision + 1,
            updated_at=max(utc_now(), product.updated_at),
        )
        values = asdict(updated)
        # Creation time and identity are immutable, even if a caller changed its snapshot.
        for name in ("id", "created_at"):
            values.pop(name)
        statement = (
            update(ProductRow)
            .where(ProductRow.id == product.id, ProductRow.revision == product.revision)
            .values(**values)
            .returning(ProductRow)
            .execution_options(populate_existing=True)
        )
        row = self._session.scalars(statement).one_or_none()
        if row is None:
            self._session.info["write_failed"] = True
            raise ConcurrentProductUpdateError(
                "Product missing or revision changed; reload before retry"
            )
        return product_from_row(row)


class SQLAlchemyEventRepository:
    def get(self, event_id: UUID) -> WorkflowEvent | None:
        row = self._session.scalar(
            select(WorkflowEventRow).where(WorkflowEventRow.event_id == event_id)
        )
        return event_from_row(row) if row is not None else None

    def reserve(self, event: WorkflowEvent) -> None:
        self.append(event)
        try:
            self._session.flush()
        except IntegrityError as exc:
            self._session.info["write_failed"] = True
            raise EventReservationError("Event identity already reserved") from exc

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: WorkflowEvent) -> None:
        try:
            payload = validated_payload(event)
        except ValueError:
            self._session.info["write_failed"] = True
            raise
        self._session.add(
            WorkflowEventRow(
                event_id=event.event_id,
                event_type=event.event_type.value,
                correlation_id=event.correlation_id,
                product_id=event.product_id,
                occurred_at=event.occurred_at,
                schema_version=event.schema_version,
                payload=payload,
            )
        )

    def list_for_workflow(self, correlation_id: UUID) -> Sequence[WorkflowEvent]:
        rows = self._session.scalars(
            select(WorkflowEventRow)
            .where(WorkflowEventRow.correlation_id == correlation_id)
            .order_by(WorkflowEventRow.sequence)
        ).all()
        return [event_from_row(row) for row in rows]

    def read_after(self, sequence: int = 0, *, limit: int = 100) -> Sequence[StoredEvent]:
        if sequence < 0 or limit < 1:
            raise ValueError("Sequence must be nonnegative and limit must be positive")
        rows = self._session.scalars(
            select(WorkflowEventRow)
            .where(WorkflowEventRow.sequence > sequence)
            .order_by(WorkflowEventRow.sequence)
            .limit(limit)
        ).all()
        return [StoredEvent(sequence=row.sequence, event=event_from_row(row)) for row in rows]
