"""Reviewed approvals with atomic event-backed command idempotency."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID, uuid4, uuid5

from stockentry_guard.application.review import validate_reviewed
from stockentry_guard.application.use_cases.check_product_duplicates import CheckProductDuplicates
from stockentry_guard.application.workflow_errors import (
    ApprovalRequiredError,
    CommandReuseError,
    DuplicateApprovalBlockedError,
    ProductNotFoundError,
)
from stockentry_guard.domain.errors import ConcurrentProductUpdateError, EventReservationError
from stockentry_guard.domain.events import EventType, WorkflowEvent
from stockentry_guard.domain.models import Product, ProductCandidate
from stockentry_guard.domain.normalization import normalize_text
from stockentry_guard.ports.unit_of_work import UnitOfWork

COMMAND_NAMESPACE = UUID("fd373fbc-b57c-441b-913c-4999d19a44aa")


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    product_id: UUID
    revision: int
    replayed: bool = False


def replay(event: WorkflowEvent, fingerprint: str) -> ApprovalResult:
    if (
        event.event_type != EventType.PRODUCT_APPROVED
        or event.payload.get("request_fingerprint") != fingerprint
    ):
        raise CommandReuseError("Command id was already used for different reviewed input")
    assert event.product_id is not None
    revision = event.payload["result_revision"]
    assert isinstance(revision, int)
    return ApprovalResult(event.product_id, revision, True)


class _Approval:
    def __init__(
        self, duplicates: CheckProductDuplicates, uow_factory: Callable[[], UnitOfWork]
    ) -> None:
        self._duplicates = duplicates
        self._uow_factory = uow_factory

    def _execute(
        self,
        candidate: ProductCandidate,
        *,
        approved: bool,
        command_id: str,
        correlation_id: UUID,
        target_id: UUID | None = None,
        expected_revision: int | None = None,
    ) -> ApprovalResult:
        if approved is not True:
            raise ApprovalRequiredError("Explicit human approval is required")
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("A nonempty command_id is required")
        reviewed = validate_reviewed(candidate)
        candidate = ProductCandidate(**reviewed.model_dump())
        action = "create" if target_id is None else "update"
        fingerprint = sha256(
            json.dumps(
                {
                    "candidate": reviewed.model_dump(mode="json"),
                    "action": action,
                    "target_id": str(target_id) if target_id else None,
                    "expected_revision": expected_revision,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        event_id = uuid5(COMMAND_NAMESPACE, command_id)
        product_id = target_id or uuid4()
        revision = 1 if expected_revision is None else expected_revision + 1
        try:
            with self._uow_factory() as uow:
                previous = uow.events.get(event_id)
                if previous is not None:
                    return replay(previous, fingerprint)
                uow.events.reserve(
                    WorkflowEvent(
                        event_type=EventType.PRODUCT_APPROVED,
                        event_id=event_id,
                        correlation_id=correlation_id,
                        product_id=product_id,
                        schema_version=2,
                        payload={
                            "action": action,
                            "command_id": command_id,
                            "request_fingerprint": fingerprint,
                            "reviewed_revision": expected_revision,
                            "result_revision": revision,
                        },
                    )
                )
                current = None
                if target_id is not None:
                    current = uow.products.get(target_id)
                    if current is None:
                        raise ProductNotFoundError("Target product does not exist")
                    if current.revision != expected_revision:
                        raise ConcurrentProductUpdateError(
                            "Product revision changed; reload and review"
                        )
                inventory = tuple(p for p in uow.products.list_all() if p.id != target_id)
                result = self._duplicates.checker.execute(candidate, inventory)
                if self._duplicates.checker.blocks_creation(result):
                    raise DuplicateApprovalBlockedError(result)
                self._duplicates.record(uow, result, correlation_id)
                values = reviewed.model_dump()
                if current is None:
                    product = Product(
                        id=product_id, normalized_name=normalize_text(reviewed.name), **values
                    )
                    uow.products.add(product)
                    event_type = EventType.PRODUCT_CREATED
                    payload = {"revision": 1}
                else:
                    product = uow.products.update(
                        replace(current, normalized_name=normalize_text(reviewed.name), **values)
                    )
                    event_type = EventType.PRODUCT_UPDATED
                    payload = {"previous_revision": current.revision, "revision": product.revision}
                uow.events.append(
                    WorkflowEvent(
                        event_type=event_type,
                        correlation_id=correlation_id,
                        product_id=product_id,
                        payload=payload,
                    )
                )
                uow.commit()
                return ApprovalResult(product_id, product.revision)
        except EventReservationError:
            with self._uow_factory() as uow:
                previous = uow.events.get(event_id)
                if previous is None:
                    raise
                return replay(previous, fingerprint)


class ApproveNewProduct(_Approval):
    def execute(
        self, candidate: ProductCandidate, *, approved: bool, command_id: str, correlation_id: UUID
    ) -> ApprovalResult:
        return self._execute(
            candidate, approved=approved, command_id=command_id, correlation_id=correlation_id
        )


class UpdateExistingProduct(_Approval):
    def execute(
        self,
        candidate: ProductCandidate,
        *,
        target_id: UUID,
        expected_revision: int,
        approved: bool,
        command_id: str,
        correlation_id: UUID,
    ) -> ApprovalResult:
        if not isinstance(target_id, UUID):
            raise ValueError("An explicit target UUID is required")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("A positive expected revision is required")
        return self._execute(
            candidate,
            approved=approved,
            command_id=command_id,
            correlation_id=correlation_id,
            target_id=target_id,
            expected_revision=expected_revision,
        )
