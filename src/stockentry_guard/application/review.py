"""Revalidation of explicitly reviewed input, shared with inventory import."""

from dataclasses import asdict

from pydantic import field_validator

from stockentry_guard.application.schemas import ProductCandidateSchema
from stockentry_guard.domain.models import ProductCandidate
from stockentry_guard.domain.normalization import normalize_text


class ReviewedProductSchema(ProductCandidateSchema):
    name: str

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        if not normalize_text(value):
            raise ValueError("Name must contain meaningful text")
        return value

    @field_validator(
        "sku",
        "barcode",
        "brand",
        "category",
        "variant",
        "size_unit",
        "description",
        mode="before",
    )
    @classmethod
    def empty_is_unknown(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


def validate_reviewed(candidate: ProductCandidate) -> ReviewedProductSchema:
    return ReviewedProductSchema.model_validate(asdict(candidate))
