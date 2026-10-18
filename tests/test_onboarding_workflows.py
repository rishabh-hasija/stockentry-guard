from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from stockentry_guard.application.use_cases.approve_product import (
    ApproveNewProduct,
    UpdateExistingProduct,
)
from stockentry_guard.application.use_cases.check_duplicates import CheckDuplicates
from stockentry_guard.application.use_cases.check_product_duplicates import CheckProductDuplicates
from stockentry_guard.application.use_cases.extract_product import ExtractProductFromImage
from stockentry_guard.application.use_cases.inventory import ImportInventory
from stockentry_guard.application.use_cases.reject_candidate import RejectCandidate
from stockentry_guard.application.workflow_errors import (
    ApprovalRequiredError,
    CommandReuseError,
    DuplicateApprovalBlockedError,
    ProductNotFoundError,
)
from stockentry_guard.domain.duplicates import DuplicateThresholds
from stockentry_guard.domain.errors import ConcurrentProductUpdateError
from stockentry_guard.domain.events import EventType
from stockentry_guard.domain.models import Classification, ProductCandidate
from stockentry_guard.infrastructure.inventory.csv_adapter import CSVInventoryImporter
from stockentry_guard.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork
from stockentry_guard.infrastructure.similarity.deterministic import DeterministicSimilarityEngine
from stockentry_guard.ports.extraction import (
    ExtractionMetadata,
    ExtractionResult,
    ProviderFailureError,
)


@pytest.fixture
def workflows(sessions):
    def factory():
        return SQLAlchemyUnitOfWork(sessions)
    checks = CheckProductDuplicates(
        CheckDuplicates(DeterministicSimilarityEngine(), DuplicateThresholds()),
        factory,
        engine_version="deterministic-v1",
        threshold_version="0.60/0.85",
    )
    return (
        factory,
        checks,
        ApproveNewProduct(checks, factory),
        UpdateExistingProduct(checks, factory),
    )


@pytest.fixture
def candidate():
    return ProductCandidate(
        name="Nutella",
        brand="Ferrero",
        size_value=Decimal("400"),
        size_unit="g",
        barcode="000123",
        category="Spread",
    )


def approve(service, candidate, **kwargs):
    return service.execute(
        candidate,
        approved=True,
        command_id=kwargs.pop("command_id", str(uuid4())),
        correlation_id=kwargs.pop("correlation_id", uuid4()),
        **kwargs,
    )


def snapshot(factory):
    with factory() as uow:
        return tuple(uow.products.list_all()), tuple(uow.events.read_after())


def test_create_and_duplicate_events(workflows, candidate):
    factory, checks, create, _ = workflows
    correlation = uuid4()
    result = approve(create, candidate, correlation_id=correlation)
    products, events = snapshot(factory)
    assert len(products) == 1 and products[0].id == result.product_id
    assert products[0].name == candidate.name
    assert [e.event.event_type for e in events] == [
        EventType.PRODUCT_APPROVED,
        EventType.DUPLICATE_CHECKED,
        EventType.PRODUCT_CREATED,
    ]
    assert events[0].event.schema_version == 2
    assert set(events[0].event.payload) == {
        "action",
        "command_id",
        "reviewed_revision",
        "request_fingerprint",
        "result_revision",
    }
    check_correlation = uuid4()
    duplicate = checks.execute(candidate, correlation_id=check_correlation)
    assert duplicate.classification == Classification.LIKELY_DUPLICATE
    with factory() as uow:
        recorded = uow.events.list_for_workflow(check_correlation)
    assert [e.event_type for e in recorded] == [
        EventType.DUPLICATE_CHECKED,
        EventType.DUPLICATE_FOUND,
    ]
    assert recorded[0].payload == {
        "classification": "LIKELY_DUPLICATE",
        "engine_version": "deterministic-v1",
        "threshold_version": "0.60/0.85",
        "top_score": duplicate.top_score,
    }
    assert len(snapshot(factory)[0]) == 1


def test_recheck_blocks_duplicate_even_after_previous_new_check(workflows, candidate):
    factory, checks, create, _ = workflows
    assert (
        checks.execute(candidate, correlation_id=uuid4()).classification
        == Classification.NEW_PRODUCT
    )
    approve(create, candidate)
    before = snapshot(factory)
    with pytest.raises(DuplicateApprovalBlockedError):
        approve(create, candidate)
    assert snapshot(factory) == before


def test_multiple_likely_matches_block_new(workflows, candidate):
    factory, _, create, _ = workflows
    # Import deliberately permits conflicts against the preexisting catalog.
    importer = ImportInventory(CSVInventoryImporter(), factory)
    for _ in range(2):
        importer.execute(b"name,barcode\nNutella,000123\n", correlation_id=uuid4(), approved=True)
    with pytest.raises(DuplicateApprovalBlockedError):
        approve(create, candidate)


def test_update_revision_full_review_and_retry(workflows, candidate):
    factory, _, create, update = workflows
    initial = approve(create, candidate)
    edited = replace(candidate, description="Reviewed description")
    command = str(uuid4())
    updated = approve(
        update, edited, target_id=initial.product_id, expected_revision=1, command_id=command
    )
    repeated = approve(
        update, edited, target_id=initial.product_id, expected_revision=1, command_id=command
    )
    assert updated.revision == repeated.revision == 2 and repeated.replayed
    products, events = snapshot(factory)
    assert products[0].description == "Reviewed description"
    assert products[0].revision == 2
    assert len(events) == 6
    assert events[-1].event.event_type == EventType.PRODUCT_UPDATED
    assert events[-1].event.payload == {"previous_revision": 1, "revision": 2}


def test_stale_and_missing_target_rollback(workflows, candidate):
    factory, _, create, update = workflows
    initial = approve(create, candidate)
    approve(update, candidate, target_id=initial.product_id, expected_revision=1)
    before = snapshot(factory)
    with pytest.raises(ConcurrentProductUpdateError):
        approve(update, candidate, target_id=initial.product_id, expected_revision=1)
    with pytest.raises(ProductNotFoundError):
        approve(update, candidate, target_id=uuid4(), expected_revision=1)
    assert snapshot(factory) == before


def test_approval_and_review_validation(workflows, candidate):
    factory, _, create, _ = workflows
    with pytest.raises(ApprovalRequiredError):
        create.execute(candidate, approved=False, command_id="no", correlation_id=uuid4())
    for invalid in [
        replace(candidate, name=None),
        replace(candidate, name=" "),
        replace(candidate, size_value=Decimal("-1")),
    ]:
        with pytest.raises(ValidationError):
            approve(create, invalid)
    with pytest.raises(ValueError):
        approve(create, candidate, command_id=" ")
    assert snapshot(factory) == ((), ())


def test_repeated_create_key_and_changed_input(workflows, candidate):
    factory, _, create, update = workflows
    result = approve(create, candidate, command_id="same")
    repeated = approve(create, candidate, command_id="same")
    assert repeated.product_id == result.product_id and repeated.replayed
    before = snapshot(factory)
    with pytest.raises(CommandReuseError):
        approve(create, replace(candidate, description="changed"), command_id="same")
    with pytest.raises(CommandReuseError):
        approve(
            update, candidate, command_id="same", target_id=result.product_id, expected_revision=1
        )
    assert snapshot(factory) == before


def test_concurrent_same_command_is_applied_once(workflows, candidate):
    factory, _, create, _ = workflows
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return approve(create, candidate, command_id="concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results[0].product_id == results[1].product_id
    assert sorted(r.replayed for r in results) == [False, True]
    products, events = snapshot(factory)
    assert len(products) == 1 and len(events) == 3


@pytest.mark.parametrize("operation", ["create", "update", "import"])
def test_transaction_failure_rolls_back_products_and_events(
    workflows, candidate, sessions, operation
):
    factory, _, create, update = workflows
    existing = approve(create, candidate) if operation == "update" else None
    before = snapshot(factory)

    def fail(session):
        raise RuntimeError("simulated commit failure")

    event.listen(sessions, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            if operation == "create":
                approve(create, candidate, command_id="retryable")
            elif operation == "update":
                approve(
                    update,
                    replace(candidate, description="edited"),
                    target_id=existing.product_id,
                    expected_revision=1,
                    command_id="retryable",
                )
            else:
                ImportInventory(CSVInventoryImporter(), factory).execute(
                    b"name\nMilk\nBread\n", approved=True, correlation_id=uuid4()
                )
    finally:
        event.remove(sessions, "before_commit", fail)
    assert snapshot(factory) == before
    if operation == "create":
        assert not approve(create, candidate, command_id="retryable").replayed


def test_rejection_is_event_only(workflows, candidate):
    factory, _, create, _ = workflows
    approve(create, candidate)
    before = snapshot(factory)[0]
    RejectCandidate(factory).execute(correlation_id=uuid4(), reason_code="USER_REJECTED")
    assert snapshot(factory)[0] == before
    assert snapshot(factory)[1][-1].event.event_type == EventType.PRODUCT_REJECTED


def test_extraction_success_and_failure_are_metadata_only(workflows, candidate):
    factory = workflows[0]
    result = ExtractionResult(
        candidate,
        ExtractionMetadata(
            confidence=0.9,
            uncertain_fields=("description",),
            model_name="fake",
            prompt_version="retail-product-v2",
            schema_version=1,
            processing_ms=4,
        ),
    )

    class Fake:
        def extract(self, image, *, media_type):
            assert image == b"private image" and media_type == "image/png"
            return result

    assert (
        ExtractProductFromImage(Fake(), factory).execute(
            b"private image", media_type="image/png", correlation_id=uuid4()
        )
        == result
    )
    products, events = snapshot(factory)
    assert not products and len(events) == 1
    assert events[0].event.payload == {
        "model_name": "fake",
        "prompt_version": "retail-product-v2",
        "schema_version": 1,
        "confidence": 0.9,
        "uncertain_field_count": 1,
        "processing_ms": 4,
    }

    class Failure:
        def extract(self, image, *, media_type):
            raise ProviderFailureError("Unavailable")

    with pytest.raises(ProviderFailureError):
        ExtractProductFromImage(Failure(), factory).execute(
            b"image", media_type="image/png", correlation_id=uuid4()
        )
    assert snapshot(factory) == (products, events)


def test_update_cannot_collide_with_another_product(workflows, candidate):
    factory, _, create, update = workflows
    first = approve(create, candidate)
    other = ProductCandidate(name="Coffee Beans", barcode="00999")
    second = approve(create, other)
    before = snapshot(factory)
    with pytest.raises(DuplicateApprovalBlockedError):
        approve(update, candidate, target_id=second.product_id, expected_revision=1)
    assert first.product_id != second.product_id
    assert snapshot(factory) == before
