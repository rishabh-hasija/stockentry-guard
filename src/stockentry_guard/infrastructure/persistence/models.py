"""SQLAlchemy mappings. Never expose these rows through domain ports."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, BigInteger, CheckConstraint, Identity, Index, Integer, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from stockentry_guard.domain.events import EventValue
from stockentry_guard.infrastructure.persistence.types import ExactDecimal, UTCDateTime


class Base(DeclarativeBase):
    pass


class ProductRow(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_products_revision"),
        CheckConstraint("length(trim(name)) > 0", name="ck_products_name"),
        CheckConstraint("length(trim(normalized_name)) > 0", name="ck_products_normalized_name"),
        Index("ix_products_barcode", "barcode"),
        Index("ix_products_sku", "sku"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sku: Mapped[str | None] = mapped_column(Text)
    barcode: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    variant: Mapped[str | None] = mapped_column(Text)
    size_value: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    size_unit: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WorkflowEventRow(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="ck_workflow_events_schema_version"),
        CheckConstraint(
            "event_type IN ('INVENTORY_IMPORTED', 'PRODUCT_EXTRACTED', 'DUPLICATE_CHECKED', "
            "'DUPLICATE_FOUND', 'PRODUCT_APPROVED', 'PRODUCT_CREATED', 'PRODUCT_UPDATED', "
            "'PRODUCT_REJECTED')",
            name="ck_workflow_events_type",
        ),
        Index("ix_workflow_events_type_occurred_at", "event_type", "occurred_at"),
        Index("ix_workflow_events_product_id", "product_id"),
        Index("ix_workflow_events_correlation_id", "correlation_id"),
        {"sqlite_autoincrement": True},
    )

    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True
    )
    event_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    event_type: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[UUID] = mapped_column(Uuid)
    product_id: Mapped[UUID | None] = mapped_column(Uuid)
    schema_version: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[dict[str, EventValue]] = mapped_column(JSON)
