"""Construct controlled extraction events; persistence belongs to future use cases."""

from uuid import UUID

from stockentry_guard.application.event_payloads import ProductExtractedPayloadV2
from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.ports.extraction import ExtractionResult


def product_extracted_event(result: ExtractionResult, correlation_id: UUID) -> WorkflowEvent:
    metadata = result.metadata
    payload = ProductExtractedPayloadV2(
        model_name=metadata.model_name,
        prompt_version=metadata.prompt_version,
        schema_version=metadata.schema_version,
        confidence=metadata.confidence,
        uncertain_field_count=len(metadata.uncertain_fields),
        processing_ms=metadata.processing_ms,
    )
    return WorkflowEvent(
        EventType.PRODUCT_EXTRACTED,
        correlation_id,
        schema_version=2,
        payload=payload.model_dump(mode="json"),
    )
