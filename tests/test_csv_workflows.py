import csv
import io
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from stockentry_guard.application.use_cases.inventory import ExportInventory, ImportInventory
from stockentry_guard.application.workflow_errors import ApprovalRequiredError
from stockentry_guard.domain.events import EventType
from stockentry_guard.domain.models import Product
from stockentry_guard.infrastructure.inventory.csv_adapter import (
    COLUMNS,
    CSVInventoryExporter,
    CSVInventoryImporter,
)
from stockentry_guard.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork
from stockentry_guard.ports.inventory import InventoryImportError


def test_valid_import_is_atomic_one_event_and_preserves_identifiers(sessions):
    def factory():
        return SQLAlchemyUnitOfWork(sessions)

    correlation = uuid4()
    result = ImportInventory(CSVInventoryImporter(), factory).execute(
        b"sku,barcode,name,size_value,size_unit\n0007,00123,Milk,0.33,l\n0008,00456,Bread,,\n",
        correlation_id=correlation,
        approved=True,
    )
    assert result.accepted_count == 2
    with factory() as uow:
        products = uow.products.list_all()
        events = uow.events.list_for_workflow(correlation)
    milk = next(p for p in products if p.name == "Milk")
    assert (milk.sku, milk.barcode, milk.size_value) == ("0007", "00123", Decimal("0.33"))
    assert len(events) == 1
    assert events[0].event_type == EventType.INVENTORY_IMPORTED
    assert events[0].payload["accepted_count"] == 2
    assert len(events[0].payload["source_fingerprint"]) == 64
    exported = ExportInventory(CSVInventoryExporter(), factory).execute()
    assert len(CSVInventoryImporter().parse(exported)) == 2


@pytest.mark.parametrize(
    "content,field",
    [
        (b"name\n   \n", "name"),
        (b"name,size_value\nMilk,nope\n", "size_value"),
        (b"name,size_value\nMilk,NaN\n", "size_value"),
        (b"name,size_value\nMilk,-1\n", "size_value"),
        (b"brand\nMilk\n", "name"),
        (b"name,name\nMilk,Milk\n", ""),
        (b"name,quantity\nMilk,4\n", "quantity"),
        (b"name\nMilk,extra\n", ""),
        (b'name\n"unclosed', ""),
        (b"name\n\xff", ""),
        (b"name\n", ""),
    ],
)
def test_structured_csv_errors(content, field):
    with pytest.raises(InventoryImportError) as error:
        CSVInventoryImporter().parse(content)
    assert error.value.errors
    assert error.value.errors[0].field == field


@pytest.mark.parametrize(
    "content",
    [
        b"name,size_value\nValid,1\nBad,nope\n",
        b"name\nMilk\nMilk\n",
        b"name,sku\nMilk,001\nBread,001\n",
        b"name,barcode\nMilk,001\nBread,001\n",
    ],
)
def test_bad_batch_writes_nothing(sessions, content):
    def factory():
        return SQLAlchemyUnitOfWork(sessions)

    with pytest.raises(InventoryImportError):
        ImportInventory(CSVInventoryImporter(), factory).execute(
            content, correlation_id=uuid4(), approved=True
        )
    with factory() as uow:
        assert not uow.products.list_all()
        assert not uow.events.read_after()


def test_import_requires_approval(sessions):
    with pytest.raises(ApprovalRequiredError):
        ImportInventory(CSVInventoryImporter(), lambda: SQLAlchemyUnitOfWork(sessions)).execute(
            b"name\nMilk\n", correlation_id=uuid4(), approved=False
        )


def test_export_columns_order_quoting_roundtrip():
    a = Product(
        id=UUID(int=1),
        name='Café, "Milk"',
        normalized_name="milk",
        sku="0007",
        barcode="00123",
        description="Line one\nLine two",
        size_value=Decimal("330.00"),
        size_unit="ml",
    )
    b = Product(id=UUID(int=2), name="Bread", normalized_name="bread")
    exported = CSVInventoryExporter().serialize([b, a])
    assert exported == CSVInventoryExporter().serialize([a, b])
    rows = list(csv.reader(io.StringIO(exported.decode())))
    assert tuple(rows[0]) == COLUMNS
    assert rows[1][2] == a.name
    imported = CSVInventoryImporter().parse(exported)[0]
    for column in COLUMNS:
        assert getattr(imported, column) == getattr(a, column)


@pytest.mark.parametrize(
    "text", ["=SUM(1,2)", "+cmd", "-cmd", "@cmd", "  =cmd", "\tcmd", "\rcmd", "\ncmd"]
)
def test_formula_protection_does_not_mutate(text):
    product = Product(id=uuid4(), name=text, normalized_name="cmd", description=text)
    before = asdict(product)
    rows = list(csv.reader(io.StringIO(CSVInventoryExporter().serialize([product]).decode())))
    assert rows[1][2] == "'" + text
    assert rows[1][-1] == "'" + text
    assert asdict(product) == before
