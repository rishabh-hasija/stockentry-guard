"""UI state decisions are browser-free and never persist inventory."""

from decimal import Decimal
from uuid import uuid4

import pytest

from stockentry_guard.application.use_cases.read_inventory import ListInventory, RecentActivity
from stockentry_guard.domain.events import EventType, StoredEvent, WorkflowEvent
from stockentry_guard.domain.models import (
    Classification,
    ProductCandidate,
    SignalScore,
    SimilarityMatch,
)
from stockentry_guard.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork
from stockentry_guard.ports.extraction import ExtractionMetadata, ExtractionResult
from stockentry_guard.ui.state import (
    checked_for_current,
    install_extraction,
    reviewed_candidate,
    sync_image,
    sync_review,
)
from stockentry_guard.ui.streamlit_app import (
    activity_label,
    activity_summary,
    classification_label,
    match_reasons,
)


def test_reviewed_fields_preserve_identifiers_and_decimal() -> None:
    candidate = reviewed_candidate(
        {
            "name": "Tea",
            "sku": "0007",
            "barcode": "00123",
            "size_value": "0.33",
            "size_unit": "l",
        }
    )
    assert candidate.sku == "0007"
    assert candidate.barcode == "00123"
    assert candidate.size_value == Decimal("0.33")
    with pytest.raises(ValueError, match="size_value|Size"):
        reviewed_candidate({"name": "Tea", "size_value": "oops"})
    with pytest.raises(ValueError, match="name"):
        reviewed_candidate({"name": "  "})


def test_reruns_keep_command_and_edits_invalidate_check() -> None:
    state: dict[str, object] = {}
    sync_image(state, "image-a")
    original = ProductCandidate(name="Tea")
    sync_review(state, original, None)
    first_key = state["create_command_id"]
    state["duplicate_result"] = object()
    state["checked_candidate"] = original
    sync_review(state, original, None)
    assert state["create_command_id"] == first_key
    assert checked_for_current(state, original)
    edited = ProductCandidate(name="Tea", variant="Mint")
    sync_review(state, edited, None)
    assert state["create_command_id"] != first_key
    assert not checked_for_current(state, edited)
    old_update_key = state["update_command_id"]
    sync_review(state, edited, (uuid4(), 2))
    assert state["update_command_id"] != old_update_key
    sync_image(state, "image-b")
    assert "reviewed_candidate" not in state
    assert "create_command_id" not in state


def test_extraction_installs_edit_values() -> None:
    state: dict[str, object] = {}
    sync_image(state, "image")
    result = ExtractionResult(
        ProductCandidate(name="Tea", barcode="0009", size_value=Decimal("250")),
        ExtractionMetadata(0.8, (), "fake", "retail-product-v2", 1, 4),
    )
    install_extraction(state, result)
    assert state["edit_name"] == "Tea"
    assert state["edit_barcode"] == "0009"
    assert state["edit_size_value"] == "250"
    assert state["edit_variant"] == ""


def test_activity_hides_approval_fingerprint() -> None:
    event = WorkflowEvent(
        EventType.PRODUCT_APPROVED,
        uuid4(),
        product_id=uuid4(),
        schema_version=2,
        payload={"action": "create", "request_fingerprint": "private", "command_id": "private"},
    )
    summary = activity_summary(StoredEvent(1, event))
    assert summary == "Create approved"
    assert "private" not in summary


@pytest.mark.parametrize(
    ("classification", "label"),
    [
        (Classification.NEW_PRODUCT, "No matching product found"),
        (Classification.NEEDS_REVIEW, "Possible match found"),
        (Classification.LIKELY_DUPLICATE, "Likely already in inventory"),
    ],
)
def test_customer_classification_labels(classification: Classification, label: str) -> None:
    assert classification_label(classification) == label


def test_customer_match_reasons_hide_internal_codes() -> None:
    match = SimilarityMatch(
        product_id=uuid4(),
        score=0.82,
        reasons=("SIZE_CONFLICT", "name: 0.900"),
        signals=(
            SignalScore("brand", 1.0, "brand: 1.000"),
            SignalScore("name", 0.9, "name: 0.900"),
            SignalScore("size", 0.0, "size: 0.000"),
            SignalScore("variant", 0.0, "variant: 0.000"),
        ),
        blockers=("SIZE_CONFLICT", "VARIANT_CONFLICT"),
    )
    assert match_reasons(match) == (
        "Same brand",
        "Similar product name",
        "Different size",
        "Different variant",
    )
    assert "SIZE_CONFLICT" not in ", ".join(match_reasons(match))


@pytest.mark.parametrize(
    ("event_type", "label"),
    [
        (EventType.PRODUCT_EXTRACTED, "Product details extracted"),
        (EventType.DUPLICATE_CHECKED, "Duplicate check completed"),
        (EventType.DUPLICATE_FOUND, "Possible duplicate found"),
        (EventType.PRODUCT_CREATED, "Product added"),
        (EventType.PRODUCT_UPDATED, "Product updated"),
        (EventType.PRODUCT_REJECTED, "Product discarded"),
        (EventType.INVENTORY_IMPORTED, "Inventory imported"),
    ],
)
def test_customer_activity_labels(event_type: EventType, label: str) -> None:
    assert activity_label(event_type) == label


def test_duplicate_activity_summary_uses_customer_wording() -> None:
    event = WorkflowEvent(
        EventType.DUPLICATE_CHECKED,
        uuid4(),
        payload={"classification": "NEW_PRODUCT", "top_score": 0.0},
    )
    assert activity_summary(StoredEvent(1, event)) == "No matching product found"


def test_read_queries_return_inventory_and_newest_events(sessions) -> None:
    def factory():
        return SQLAlchemyUnitOfWork(sessions)

    with factory() as uow:
        for index in range(4):
            uow.events.append(
                WorkflowEvent(
                    EventType.PRODUCT_REJECTED, uuid4(), payload={"reason_code": str(index)}
                )
            )
        uow.commit()
    assert ListInventory(factory).execute() == ()
    recent = RecentActivity(factory).execute(limit=2)
    assert [item.event.payload["reason_code"] for item in recent] == ["3", "2"]
    with pytest.raises(ValueError, match="positive"):
        RecentActivity(factory).execute(limit=0)
