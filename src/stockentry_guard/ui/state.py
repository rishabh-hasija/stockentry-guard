"""Transient review state and display mapping, independent of Streamlit widgets."""

from collections.abc import Mapping, MutableMapping
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from pydantic import ValidationError

from stockentry_guard.application.review import ReviewedProductSchema
from stockentry_guard.domain.models import ProductCandidate
from stockentry_guard.ports.extraction import ExtractionResult

FIELDS = (
    "name",
    "sku",
    "barcode",
    "brand",
    "category",
    "variant",
    "size_value",
    "size_unit",
    "description",
)


def clear_review(state: MutableMapping[str, object]) -> None:
    for key in (
        "extraction",
        "reviewed_candidate",
        "checked_candidate",
        "duplicate_result",
        "create_command_id",
        "update_command_id",
        "update_target",
        "update_revision",
        "selected_target",
    ):
        state.pop(key, None)
    for field in FIELDS:
        state.pop(f"edit_{field}", None)


def sync_image(state: MutableMapping[str, object], digest: str | None) -> None:
    if state.get("image_digest") == digest:
        return
    clear_review(state)
    state["image_digest"] = digest
    state["workflow_id"] = uuid4()


def install_extraction(state: MutableMapping[str, object], result: ExtractionResult) -> None:
    clear_review(state)
    state["extraction"] = result
    for field in FIELDS:
        value = getattr(result.candidate, field)
        state[f"edit_{field}"] = "" if value is None else str(value)


def reviewed_candidate(values: Mapping[str, str]) -> ProductCandidate:
    data: dict[str, object] = {field: values.get(field, "") for field in FIELDS}
    size = str(data["size_value"]).strip()
    try:
        data["size_value"] = Decimal(size) if size else None
    except InvalidOperation as exc:
        raise ValueError("Size must be a positive number") from exc
    try:
        reviewed = ReviewedProductSchema.model_validate(data)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        field = first["loc"][0]
        raise ValueError(f"{field}: {first['msg']}") from exc
    return ProductCandidate(**reviewed.model_dump())


def sync_review(
    state: MutableMapping[str, object],
    candidate: ProductCandidate,
    target: tuple[UUID, int] | None,
) -> None:
    if state.get("reviewed_candidate") != candidate:
        state["reviewed_candidate"] = candidate
        state.pop("checked_candidate", None)
        state.pop("duplicate_result", None)
        state["create_command_id"] = str(uuid4())
        state["update_command_id"] = str(uuid4())
    if state.get("update_target") != target:
        state["update_target"] = target
        state["update_command_id"] = str(uuid4())


def invalidate_review(state: MutableMapping[str, object]) -> None:
    state.pop("reviewed_candidate", None)
    state.pop("checked_candidate", None)
    state.pop("duplicate_result", None)


def checked_for_current(state: Mapping[str, object], candidate: ProductCandidate) -> bool:
    return state.get("checked_candidate") == candidate and state.get("duplicate_result") is not None
