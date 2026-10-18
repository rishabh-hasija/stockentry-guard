"""Untrusted candidate boundary schema; approval is a separate future use case."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductCandidateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = None
    sku: str | None = None
    barcode: str | None = None
    brand: str | None = None
    category: str | None = None
    variant: str | None = None
    size_value: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    size_unit: str | None = None
    description: str | None = None
