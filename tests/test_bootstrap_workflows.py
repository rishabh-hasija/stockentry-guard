"""Composition root exposes every completed workflow without contacting Ollama."""

from uuid import uuid4

from sqlalchemy import Engine

from stockentry_guard.bootstrap import build_workflows
from stockentry_guard.config import Settings
from stockentry_guard.domain.events import EventType
from stockentry_guard.domain.models import ProductCandidate


def test_composed_workflows_import_create_check_reject_export(engine: Engine) -> None:
    workflows = build_workflows(Settings(_env_file=None, database_url=str(engine.url)))
    imported = workflows.import_inventory.execute(
        b"sku,barcode,name\n001,000123,Milk\n", correlation_id=uuid4(), approved=True
    )
    assert imported.accepted_count == 1
    created = workflows.approve_new_product.execute(
        ProductCandidate(name="Bread"),
        approved=True,
        command_id="bootstrap-test",
        correlation_id=uuid4(),
    )
    assert created.revision == 1
    check = workflows.check_product_duplicates.execute(
        ProductCandidate(name="Milk", barcode="000123"), correlation_id=uuid4()
    )
    assert check.matched_product_id == imported.product_ids[0]
    rejected = workflows.reject_candidate.execute(correlation_id=uuid4())
    assert rejected.event_type == EventType.PRODUCT_REJECTED
    exported = workflows.export_inventory.execute()
    assert b"000123,Milk" in exported
    assert b"Bread" in exported
