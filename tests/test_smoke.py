"""Installed contracts work offline without a database or model."""

from uuid import uuid4

from stockentry_guard.application.schemas import ProductCandidateSchema
from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.domain.models import Classification, ProductCandidate


def test_candidate_and_event_contracts() -> None:
    schema = ProductCandidateSchema(name="Example tea", barcode="0012345")
    candidate = ProductCandidate(**schema.model_dump())
    workflow_id = uuid4()
    event = WorkflowEvent(
        event_type=EventType.PRODUCT_EXTRACTED,
        correlation_id=workflow_id,
        payload={"classification": Classification.NEEDS_REVIEW.value},
    )

    assert candidate.barcode == "0012345"
    assert candidate.brand is None
    assert event.correlation_id == workflow_id
    assert event.product_id is None
    assert event.occurred_at.utcoffset() is not None
    assert event.schema_version == 1
