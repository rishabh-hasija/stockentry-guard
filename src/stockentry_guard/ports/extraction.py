"""Provider-neutral extraction results and service errors."""

from dataclasses import dataclass
from typing import Literal

from stockentry_guard.domain.models import ProductCandidate

type ProductField = Literal[
    "name",
    "sku",
    "barcode",
    "brand",
    "category",
    "variant",
    "size_value",
    "size_unit",
    "description",
]


@dataclass(frozen=True, slots=True)
class ExtractionMetadata:
    """Confidence is a model-reported heuristic, not a calibrated probability."""

    confidence: float
    uncertain_fields: tuple[ProductField, ...]
    model_name: str
    prompt_version: str
    schema_version: int
    processing_ms: int


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    candidate: ProductCandidate
    metadata: ExtractionMetadata


class ExtractionError(Exception):
    """Safe service error, containing no raw provider response or exception text."""


class UnsupportedImageTypeError(ExtractionError):
    pass


class OversizedImageError(ExtractionError):
    pass


class InvalidImageError(ExtractionError):
    pass


class OllamaUnavailableError(ExtractionError):
    pass


class OllamaTimeoutError(ExtractionError):
    pass


class ProviderFailureError(ExtractionError):
    pass


class InvalidModelResponseError(ExtractionError):
    pass


class MalformedJSONError(InvalidModelResponseError):
    pass


class ExtractionSchemaError(InvalidModelResponseError):
    pass


class MissingProductNameError(InvalidModelResponseError):
    pass
