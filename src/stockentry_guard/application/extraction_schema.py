"""Strict model-output validation, independent of the inference provider."""

import json
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from stockentry_guard.domain.models import ProductCandidate
from stockentry_guard.ports.extraction import (
    ExtractionSchemaError,
    MalformedJSONError,
    MissingProductNameError,
    ProductField,
)

EXTRACTION_SCHEMA_VERSION = 1


class ExtractionResponseSchema(BaseModel):
    """Size is a decimal string on the wire, avoiding binary floating-point loss."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None
    sku: str | None
    barcode: str | None
    brand: str | None
    category: str | None
    variant: str | None
    size_value: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")] | None
    size_unit: str | None
    description: str | None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    uncertain_fields: list[ProductField]

    @field_validator(
        "name",
        "sku",
        "barcode",
        "brand",
        "category",
        "variant",
        "size_value",
        "size_unit",
        "description",
        mode="before",
    )
    @classmethod
    def blank_is_unknown(cls, value: object) -> object:
        # Do not coerce numeric identifiers or alter nonblank identifier strings.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("size_value")
    @classmethod
    def positive_size(cls, value: str | None) -> str | None:
        if value is not None and Decimal(value) <= 0:
            raise ValueError("Size must be positive")
        return value

    @model_validator(mode="after")
    def consistent_uncertainty(self) -> Self:
        if len(self.uncertain_fields) != len(set(self.uncertain_fields)):
            raise ValueError("Uncertain field names must be unique")
        if any(getattr(self, name) is not None for name in self.uncertain_fields):
            raise ValueError("Uncertain values must be null")
        return self

    def to_candidate(self) -> ProductCandidate:
        if self.name is None or self.name.strip().casefold() in {
            "unknown",
            "unknown product",
            "unidentified",
            "product",
            "n/a",
            "none",
            "null",
        }:
            raise MissingProductNameError("No meaningful product name was extracted")
        return ProductCandidate(
            name=self.name,
            sku=self.sku,
            barcode=self.barcode,
            brand=self.brand,
            category=self.category,
            variant=self.variant,
            size_value=Decimal(self.size_value) if self.size_value is not None else None,
            size_unit=self.size_unit,
            description=self.description,
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Nonstandard JSON constant")


def parse_extraction_response(content: str) -> ExtractionResponseSchema:
    try:
        data = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise MalformedJSONError("Model response is not a valid JSON object") from None
    try:
        return ExtractionResponseSchema.model_validate(data)
    except ValidationError:
        raise ExtractionSchemaError("Model response does not match the extraction schema") from None
