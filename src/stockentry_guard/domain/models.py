"""Product identity and invariants, independent of persistence and UI."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(UTC)


class Classification(StrEnum):
    NEW_PRODUCT = "NEW_PRODUCT"
    LIKELY_DUPLICATE = "LIKELY_DUPLICATE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductCandidate:
    """Unapproved data: never automatically converted into inventory."""

    name: str | None = None
    sku: str | None = None
    barcode: str | None = None
    brand: str | None = None
    category: str | None = None
    variant: str | None = None
    size_value: Decimal | None = None
    size_unit: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Product:
    """Reviewed inventory record. UUID, not name/SKU/barcode, is its identity."""

    id: UUID
    name: str
    normalized_name: str
    sku: str | None = None
    barcode: str | None = None
    brand: str | None = None
    category: str | None = None
    variant: str | None = None
    size_value: Decimal | None = None
    size_unit: str | None = None
    description: str | None = None
    revision: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("Product id must be a UUID")
        for name in ("name", "normalized_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonblank string")
        for name in ("sku", "barcode", "brand", "category", "variant", "size_unit", "description"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Revision must be a positive integer")
        if self.size_value is not None and (
            not isinstance(self.size_value, Decimal)
            or not self.size_value.is_finite()
            or self.size_value <= 0
        ):
            raise ValueError("Size must be a positive finite Decimal")
        object.__setattr__(self, "created_at", as_utc(self.created_at))
        object.__setattr__(self, "updated_at", as_utc(self.updated_at))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")


@dataclass(frozen=True, slots=True)
class SignalScore:
    name: str
    score: float | None
    explanation: str


@dataclass(frozen=True, slots=True)
class SimilarityMatch:
    """Advisory evidence, not permission to mutate inventory."""

    product_id: UUID
    score: float
    reasons: tuple[str, ...]
    signals: tuple[SignalScore, ...] = ()
    blockers: tuple[str, ...] = ()
