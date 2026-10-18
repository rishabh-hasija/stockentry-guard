import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from stockentry_guard.domain.errors import ConcurrentProductUpdateError, TransactionAbortedError
from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.domain.models import Product, ProductCandidate
from stockentry_guard.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


def product(**overrides: object) -> Product:
    values = {"id": uuid4(), "name": "Tea", "normalized_name": "tea"}
    values.update(overrides)
    return Product(**values)


def created(value: Product, **overrides: object) -> WorkflowEvent:
    values = {
        "event_type": EventType.PRODUCT_CREATED,
        "correlation_id": uuid4(),
        "product_id": value.id,
        "payload": {"revision": 1},
    }
    values.update(overrides)
    return WorkflowEvent(**values)


def test_insert_mapping_and_exact_round_trip(sessions: sessionmaker[Session]) -> None:
    timestamp = datetime(2026, 10, 18, 11, 12, 13, 123456, timezone(timedelta(hours=2)))
    original = product(
        sku="00007",
        barcode="0000012345678",
        brand="Example",
        category="Tea",
        variant="Herbal",
        size_value=Decimal("12.12345678901234567890"),
        size_unit="g",
        description="Loose tea",
        created_at=timestamp,
        updated_at=timestamp,
    )
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(original)
        assert uow.products.get(original.id) == original
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        actual = uow.products.get(original.id)
        assert actual == original
        assert actual.id == original.id
        assert actual.size_value.as_tuple() == original.size_value.as_tuple()
        assert actual.created_at.tzinfo is UTC
        assert actual.updated_at.tzinfo is UTC
        assert uow.products.list_all() == [original]
        assert uow.products.get(uuid4()) is None


@pytest.mark.parametrize("sku,barcode", [(None, None), ("00001", None), (None, "00123")])
def test_optional_identifiers(sessions: sessionmaker[Session], sku: str, barcode: str) -> None:
    original = product(sku=sku, barcode=barcode)
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(original)
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.get(original.id) == original


def test_name_and_external_ids_do_not_redefine_identity(sessions: sessionmaker[Session]) -> None:
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(product(barcode="123", size_value=Decimal(100), size_unit="ml"))
        uow.products.add(product(barcode="123", size_value=Decimal(200), size_unit="ml"))
        uow.products.add(product(variant="Sugarfree"))
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert len(uow.products.list_all()) == 3


def test_candidate_cannot_be_added(sessions: sessionmaker[Session]) -> None:
    with SQLAlchemyUnitOfWork(sessions) as uow:
        with pytest.raises(TypeError):
            uow.products.add(ProductCandidate(name="unapproved"))


def test_update_revision_and_immutable_created_at(sessions: sessionmaker[Session]) -> None:
    original = product()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(original)
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        changed = uow.products.update(
            replace(
                original,
                name="New tea",
                normalized_name="new tea",
                created_at=original.created_at - timedelta(days=1),
            )
        )
        assert changed.revision == 2
        assert changed.created_at == original.created_at
        assert changed.updated_at >= original.updated_at
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.get(original.id) == changed


def test_stale_snapshot_cannot_overwrite_or_commit_event(sessions: sessionmaker[Session]) -> None:
    original = product()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(original)
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        newer = uow.products.update(replace(original, name="Winner"))
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.events.append(WorkflowEvent(EventType.PRODUCT_REJECTED, uuid4()))
        with pytest.raises(ConcurrentProductUpdateError):
            uow.products.update(replace(original, name="Stale"))
        with pytest.raises(TransactionAbortedError):
            uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.get(original.id) == newer
        assert uow.events.read_after() == []


def test_missing_update_does_not_insert(sessions: sessionmaker[Session]) -> None:
    with pytest.raises(ConcurrentProductUpdateError), SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.update(product())
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.list_all() == []


def test_atomic_product_and_event_commit(sessions: sessionmaker[Session]) -> None:
    original = product()
    event = created(original)
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(original)
        uow.events.append(event)
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.get(original.id) == original
        assert uow.events.list_for_workflow(event.correlation_id) == [event]
        assert uow.events.list_for_workflow(uuid4()) == []
        stored = uow.events.read_after()[0]
        assert stored.event == event
        assert stored.event.occurred_at.tzinfo is UTC
        assert stored.sequence > 0


@pytest.mark.parametrize("mode", ["implicit", "explicit", "exception"])
def test_rollback_discards_product_and_event(sessions: sessionmaker[Session], mode: str) -> None:
    original = product()
    try:
        with SQLAlchemyUnitOfWork(sessions) as uow:
            uow.products.add(original)
            uow.events.append(created(original))
            # Force SQL writes, proving exit rolls back more than an unflushed queue.
            assert uow.products.get(original.id) is not None
            if mode == "explicit":
                uow.rollback()
            if mode == "exception":
                raise RuntimeError("simulated failure")
    except RuntimeError:
        assert mode == "exception"
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.list_all() == []
        assert uow.events.read_after() == []


def test_failed_event_insert_rolls_back_product_update(sessions: sessionmaker[Session]) -> None:
    original = product()
    event = created(original)
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(original)
        uow.events.append(event)
        uow.commit()
    with pytest.raises(IntegrityError), SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.update(replace(original, name="Must roll back"))
        uow.events.append(replace(event, event_id=uuid4()))
        uow.events.append(event)  # unique event UUID violation
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.get(original.id) == original
        assert len(uow.events.read_after()) == 1


def test_failed_product_insert_rolls_back_event(sessions: sessionmaker[Session]) -> None:
    original = product()
    with pytest.raises(IntegrityError), SQLAlchemyUnitOfWork(sessions) as uow:
        uow.products.add(original)
        uow.products.add(original)
        uow.events.append(created(original))
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.products.list_all() == []
        assert uow.events.read_after() == []


def test_monotonic_sequence_and_pagination(sessions: sessionmaker[Session]) -> None:
    workflow = uuid4()
    for _ in range(3):
        with SQLAlchemyUnitOfWork(sessions) as uow:
            uow.events.append(WorkflowEvent(EventType.PRODUCT_REJECTED, workflow))
            uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        events = uow.events.read_after()
        sequences = [event.sequence for event in events]
        assert sequences[0] < sequences[1] < sequences[2]
        assert uow.events.read_after(sequences[0], limit=1) == [events[1]]
        assert uow.events.read_after(sequences[-1]) == []
        assert len(uow.events.list_for_workflow(workflow)) == 3


def test_uow_lifecycle_guards(sessions: sessionmaker[Session]) -> None:
    uow = SQLAlchemyUnitOfWork(sessions)
    with pytest.raises(RuntimeError):
        uow.commit()
    with uow:
        uow.commit()
        with pytest.raises(RuntimeError):
            _ = uow.products
    with pytest.raises(RuntimeError):
        with uow:
            pass


def test_sample_csv_strings_and_decimal_persist(sessions: sessionmaker[Session]) -> None:
    # A fixture-loading test, not a CSV importer/use-case implementation.
    with Path("sample_data/inventory.csv").open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert 15 <= len(rows) <= 20
    with SQLAlchemyUnitOfWork(sessions) as uow:
        for row in rows:
            uow.products.add(
                product(
                    **{key: value or None for key, value in row.items() if key != "size_value"},
                    normalized_name=row["name"].casefold(),
                    size_value=Decimal(row["size_value"]) if row["size_value"] else None,
                )
            )
        event = WorkflowEvent(
            EventType.INVENTORY_IMPORTED,
            uuid4(),
            payload={"accepted_count": len(rows), "source_fingerprint": "fixture"},
        )
        uow.events.append(event)
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        values = uow.products.list_all()
        cola = next(p for p in values if p.name == "Coca-Cola Original 330ml")
        assert cola.sku == "00001"
        assert cola.barcode == "0000000000001"
        assert cola.size_value == Decimal("330")
        assert len(values) == len(rows)
        assert [entry.event.event_type for entry in uow.events.read_after()] == [
            EventType.INVENTORY_IMPORTED
        ]


def test_schema_has_only_requested_tables_and_indexes(engine: Engine) -> None:
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {"products", "workflow_events", "alembic_version"}
    assert inspector.get_pk_constraint("workflow_events")["constrained_columns"] == ["sequence"]
    assert {item["name"] for item in inspector.get_indexes("workflow_events")} == {
        "ix_workflow_events_type_occurred_at",
        "ix_workflow_events_product_id",
        "ix_workflow_events_correlation_id",
    }
