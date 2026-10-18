"""Allowlisted version-one event payloads, never arbitrary model responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stockentry_guard.domain.events import EventType, EventValue, WorkflowEvent


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class InventoryImportedPayload(EventPayload):
    accepted_count: int = Field(ge=0)
    source_fingerprint: str = Field(min_length=1)


class ProductExtractedPayload(EventPayload):
    model_tag: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    missing_field_count: int = Field(ge=0)


class ProductExtractedPayloadV2(EventPayload):
    model_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    uncertain_field_count: int = Field(ge=0, le=9)
    processing_ms: int = Field(ge=0)


class DuplicateCheckedPayload(EventPayload):
    classification: Literal["NEW_PRODUCT", "LIKELY_DUPLICATE", "NEEDS_REVIEW"]
    engine_version: str = Field(min_length=1)
    threshold_version: str = Field(min_length=1)
    top_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class DuplicateFoundPayload(EventPayload):
    matched_product_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason_code: str = Field(min_length=1)


class ProductApprovedPayload(EventPayload):
    action: Literal["create", "update"]
    reviewed_revision: int | None = Field(default=None, ge=1)
    command_id: str = Field(min_length=1)


class ProductApprovedPayloadV2(ProductApprovedPayload):
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_revision: int = Field(ge=1)


class ProductCreatedPayload(EventPayload):
    revision: Literal[1] = 1


class ProductUpdatedPayload(EventPayload):
    previous_revision: int = Field(ge=1)
    revision: int = Field(ge=2)


class ProductRejectedPayload(EventPayload):
    reason_code: str | None = None


PAYLOAD_MODELS: dict[EventType, type[EventPayload]] = {
    EventType.INVENTORY_IMPORTED: InventoryImportedPayload,
    EventType.PRODUCT_EXTRACTED: ProductExtractedPayload,
    EventType.DUPLICATE_CHECKED: DuplicateCheckedPayload,
    EventType.DUPLICATE_FOUND: DuplicateFoundPayload,
    EventType.PRODUCT_APPROVED: ProductApprovedPayload,
    EventType.PRODUCT_CREATED: ProductCreatedPayload,
    EventType.PRODUCT_UPDATED: ProductUpdatedPayload,
    EventType.PRODUCT_REJECTED: ProductRejectedPayload,
}


def validated_payload(event: WorkflowEvent) -> dict[str, EventValue]:
    if event.event_type == EventType.PRODUCT_APPROVED and event.schema_version == 2:
        if event.product_id is None:
            raise ValueError("Approval event requires product id")
        return dict(ProductApprovedPayloadV2.model_validate(dict(event.payload)).model_dump())
    if event.event_type == EventType.PRODUCT_EXTRACTED and event.schema_version == 2:
        return dict(ProductExtractedPayloadV2.model_validate(dict(event.payload)).model_dump())
    if event.schema_version != 1:
        raise ValueError(f"Unsupported event schema version: {event.schema_version}")
    model = PAYLOAD_MODELS[event.event_type].model_validate(dict(event.payload))
    if isinstance(model, ProductUpdatedPayload) and model.revision != model.previous_revision + 1:
        raise ValueError("Updated event revisions must be consecutive")
    if event.event_type in {EventType.PRODUCT_CREATED, EventType.PRODUCT_UPDATED}:
        if event.product_id is None:
            raise ValueError("Product mutation events require a product id")
    return dict(model.model_dump(mode="json"))
