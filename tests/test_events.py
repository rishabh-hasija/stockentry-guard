from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from stockentry_guard.application.event_payloads import validated_payload
from stockentry_guard.domain.errors import TransactionAbortedError
from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


@pytest.mark.parametrize(
    "event_type,payload",
    [
        (EventType.INVENTORY_IMPORTED, {"accepted_count": 18, "source_fingerprint": "abc"}),
        (
            EventType.PRODUCT_EXTRACTED,
            {
                "model_tag": "gemma3:4b",
                "prompt_version": "v1",
                "missing_field_count": 2,
            },
        ),
        (
            EventType.DUPLICATE_CHECKED,
            {
                "classification": "NEW_PRODUCT",
                "engine_version": "v1",
                "threshold_version": "v1",
                "top_score": 0.1,
            },
        ),
        (
            EventType.DUPLICATE_FOUND,
            {
                "matched_product_id": str(uuid4()),
                "score": 0.9,
                "reason_code": "barcode",
            },
        ),
        (
            EventType.PRODUCT_APPROVED,
            {
                "action": "create",
                "reviewed_revision": None,
                "command_id": "approval-001",
            },
        ),
        (EventType.PRODUCT_CREATED, {"revision": 1}),
        (EventType.PRODUCT_UPDATED, {"previous_revision": 1, "revision": 2}),
        (EventType.PRODUCT_REJECTED, {"reason_code": "bad_image"}),
    ],
)
def test_all_event_types_round_trip(sessions: sessionmaker[Session], event_type, payload) -> None:
    event = WorkflowEvent(event_type, uuid4(), product_id=uuid4(), payload=payload)
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.events.append(event)
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.events.list_for_workflow(event.correlation_id) == [event]


@pytest.mark.parametrize(
    "payload",
    [
        {"raw_model_response": '{"anything": "here"}'},
        {"reason_code": {"nested": "json"}},
    ],
)
def test_arbitrary_model_payload_rejected_and_transaction_aborted(
    sessions: sessionmaker[Session],
    payload,
) -> None:
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.events.append(WorkflowEvent(EventType.PRODUCT_REJECTED, uuid4()))
        with pytest.raises(ValueError):
            uow.events.append(WorkflowEvent(EventType.PRODUCT_REJECTED, uuid4(), payload=payload))
        with pytest.raises(TransactionAbortedError):
            uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.events.read_after() == []


def test_unknown_payload_version_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        validated_payload(WorkflowEvent(EventType.PRODUCT_REJECTED, uuid4(), schema_version=2))


def test_mutation_event_needs_product_identity() -> None:
    with pytest.raises(ValueError, match="product id"):
        validated_payload(WorkflowEvent(EventType.PRODUCT_CREATED, uuid4()))


def test_update_payload_requires_consecutive_revisions() -> None:
    with pytest.raises(ValueError, match="consecutive"):
        validated_payload(
            WorkflowEvent(
                EventType.PRODUCT_UPDATED,
                uuid4(),
                product_id=uuid4(),
                payload={"previous_revision": 1, "revision": 3},
            )
        )
