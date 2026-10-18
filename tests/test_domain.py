from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.domain.models import Product


def product() -> Product:
    return Product(id=uuid4(), name="Tea", normalized_name="tea")


def test_optional_identifiers_and_defaults() -> None:
    value = product()
    assert value.sku is None
    assert value.barcode is None
    assert value.revision == 1
    assert value.created_at.tzinfo is UTC


@pytest.mark.parametrize("field", ["name", "normalized_name"])
@pytest.mark.parametrize("value", ["", "   "])
def test_nonblank_names(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        replace(product(), **{field: value})


@pytest.mark.parametrize("size", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_decimal_sizes(size: Decimal) -> None:
    with pytest.raises(ValueError):
        replace(product(), size_value=size)


def test_no_float_coercion() -> None:
    with pytest.raises(ValueError):
        replace(product(), size_value=0.1)


@pytest.mark.parametrize("revision", [0, -1, True])
def test_invalid_revision(revision: int) -> None:
    with pytest.raises(ValueError):
        replace(product(), revision=revision)


def test_utc_normalization_and_naive_timestamp_rejection() -> None:
    local = datetime(2026, 10, 18, 10, tzinfo=timezone(timedelta(hours=2)))
    value = replace(product(), created_at=local, updated_at=local)
    assert value.created_at == datetime(2026, 10, 18, 8, tzinfo=UTC)
    assert value.created_at.tzinfo is UTC
    with pytest.raises(ValueError):
        replace(value, created_at=datetime(2026, 10, 18))
    with pytest.raises(ValueError):
        replace(value, updated_at=value.created_at - timedelta(seconds=1))


def test_event_payload_is_copied_and_immutable() -> None:
    payload = {"reason_code": "mistake"}
    event = WorkflowEvent(EventType.PRODUCT_REJECTED, uuid4(), payload=payload)
    payload["reason_code"] = "changed"
    assert event.payload["reason_code"] == "mistake"
    with pytest.raises(TypeError):
        event.payload["reason_code"] = "changed"
