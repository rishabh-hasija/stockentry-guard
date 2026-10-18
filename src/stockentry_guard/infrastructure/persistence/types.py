"""Lossless Decimal and UTC adapters across SQLite and PostgreSQL."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from stockentry_guard.domain.models import as_utc


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores UTC wall time; all values returned to callers are aware UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        value = as_utc(value)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ExactDecimal(TypeDecorator[Decimal]):
    """Text avoids SQLite binary-float coercion; numeric SQL ordering is not supported."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise ValueError("Size must be a positive finite Decimal")
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        return Decimal(value) if value is not None else None
