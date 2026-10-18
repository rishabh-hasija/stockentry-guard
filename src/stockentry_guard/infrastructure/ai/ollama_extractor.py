"""Local Ollama adapter. No inventory or event persistence occurs here."""

import json
from time import perf_counter

import httpx
from ollama import Client, ResponseError
from pydantic import ValidationError

from stockentry_guard.application.extraction_schema import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractionResponseSchema,
    parse_extraction_response,
)
from stockentry_guard.infrastructure.ai.prompts import EXTRACTION_PROMPT, PROMPT_VERSION
from stockentry_guard.ports.extraction import (
    ExtractionMetadata,
    ExtractionResult,
    InvalidImageError,
    InvalidModelResponseError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    OversizedImageError,
    ProviderFailureError,
    UnsupportedImageTypeError,
)

SUPPORTED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class OllamaProductExtractor:
    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
        max_image_bytes: int,
        client: Client | None = None,
    ) -> None:
        if max_image_bytes < 1 or timeout_seconds <= 0 or not model_name.strip():
            raise ValueError("Invalid extraction configuration")
        self._model_name = model_name
        self._max_image_bytes = max_image_bytes
        self._client = (
            client
            if client is not None
            else Client(
                host=base_url,
                timeout=timeout_seconds,
            )
        )

    def extract(self, image: bytes, *, media_type: str) -> ExtractionResult:
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise UnsupportedImageTypeError("Supported image types are JPEG, PNG, and WebP")
        if len(image) > self._max_image_bytes:
            raise OversizedImageError(f"Image exceeds the {self._max_image_bytes}-byte limit")
        if not image:
            raise InvalidImageError("Image is empty")
        schema = ExtractionResponseSchema.model_json_schema()
        started = perf_counter()
        try:
            response = self._client.chat(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": EXTRACTION_PROMPT},
                    {
                        "role": "user",
                        "content": "Extract this product using this schema: " + json.dumps(schema),
                        "images": [image],
                    },
                ],
                format=schema,
                options={"temperature": 0, "num_predict": 1024},
                stream=False,
            )
        except httpx.TimeoutException:
            raise OllamaTimeoutError("Local model inference timed out") from None
        except (ConnectionError, httpx.ConnectError):
            raise OllamaUnavailableError(
                "Cannot connect to the configured Ollama service"
            ) from None
        except ResponseError:
            raise ProviderFailureError(
                "Ollama rejected extraction; check model availability"
            ) from None
        except (ValidationError, json.JSONDecodeError):
            raise InvalidModelResponseError(
                "Ollama returned an invalid response envelope"
            ) from None
        except httpx.HTTPError:
            raise ProviderFailureError("Ollama transport failed during extraction") from None
        except Exception:
            raise ProviderFailureError("Unexpected local provider failure") from None
        if response.done is not True or response.done_reason == "length":
            raise InvalidModelResponseError("Ollama returned an incomplete response")
        content = response.message.content
        if not isinstance(content, str) or not content.strip():
            raise InvalidModelResponseError("Ollama returned no extraction content")
        parsed = parse_extraction_response(content)
        candidate = parsed.to_candidate()
        return ExtractionResult(
            candidate=candidate,
            metadata=ExtractionMetadata(
                confidence=parsed.confidence,
                uncertain_fields=tuple(parsed.uncertain_fields),
                model_name=self._model_name,
                prompt_version=PROMPT_VERSION,
                schema_version=EXTRACTION_SCHEMA_VERSION,
                processing_ms=max(0, round((perf_counter() - started) * 1000)),
            ),
        )
