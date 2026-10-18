import json
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import pytest
from ollama import ChatResponse, Client, Message, ResponseError

from stockentry_guard.application.event_payloads import validated_payload
from stockentry_guard.application.extraction_events import product_extracted_event
from stockentry_guard.application.extraction_schema import (
    ExtractionResponseSchema,
    parse_extraction_response,
)
from stockentry_guard.bootstrap import build_product_extractor
from stockentry_guard.config import Settings
from stockentry_guard.infrastructure.ai.ollama_extractor import OllamaProductExtractor
from stockentry_guard.infrastructure.ai.prompts import EXTRACTION_PROMPT, PROMPT_VERSION
from stockentry_guard.ports.extraction import (
    ExtractionSchemaError,
    InvalidImageError,
    InvalidModelResponseError,
    MalformedJSONError,
    MissingProductNameError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    OversizedImageError,
    ProviderFailureError,
    UnsupportedImageTypeError,
)

IMAGE = b"fake-image-bytes"


def extractor(content=None, *, error=None, done=True, reason="stop"):
    client = Mock(spec=Client)
    client.chat.return_value = ChatResponse(
        model="configured-vision-model",
        done=done,
        done_reason=reason,
        message=Message(role="assistant", content=content),
    )
    client.chat.side_effect = error
    adapter = OllamaProductExtractor(
        base_url="http://localhost:12345",
        model_name="configured-vision-model",
        timeout_seconds=12,
        max_image_bytes=1000,
        client=client,
    )
    return adapter, client


def valid(**changes):
    result = {
        "name": "Oat Drink",
        "sku": None,
        "barcode": None,
        "brand": None,
        "category": None,
        "variant": None,
        "size_value": None,
        "size_unit": None,
        "description": None,
        "confidence": 0.8,
        "uncertain_fields": [],
    }
    result.update(changes)
    return json.dumps(result)


def test_valid_extraction_and_request_contract():
    adapter, client = extractor(
        valid(
            brand="Harbor",
            variant="Original",
            size_value="330",
            size_unit="ml",
            uncertain_fields=["barcode"],
        )
    )
    result = adapter.extract(IMAGE, media_type="image/png")
    assert result.candidate.name == "Oat Drink"
    assert result.candidate.brand == "Harbor"
    assert result.candidate.size_value == Decimal("330")
    assert result.candidate.barcode is None
    assert result.metadata.confidence == 0.8
    assert result.metadata.uncertain_fields == ("barcode",)
    assert result.metadata.model_name == "configured-vision-model"
    assert result.metadata.prompt_version == PROMPT_VERSION == "retail-product-v2"
    assert result.metadata.schema_version == 1
    assert result.metadata.processing_ms >= 0
    kwargs = client.chat.call_args.kwargs
    assert kwargs["model"] == "configured-vision-model"
    assert kwargs["format"] == ExtractionResponseSchema.model_json_schema()
    assert kwargs["stream"] is False
    assert kwargs["options"]["temperature"] == 0
    assert kwargs["messages"][0] == {"role": "system", "content": EXTRACTION_PROMPT}
    assert kwargs["messages"][1]["images"] == [IMAGE]


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/webp"])
def test_supported_mime_and_null_product_fields(mime):
    adapter, _ = extractor(valid())
    result = adapter.extract(IMAGE, media_type=mime)
    assert result.candidate.sku is None
    assert result.candidate.barcode is None
    assert result.candidate.size_value is None
    assert result.candidate.description is None
    assert result.metadata.uncertain_fields == ()


def test_identifiers_and_decimal_preserved():
    adapter, _ = extractor(
        valid(
            sku="000012",
            barcode="0000123456789",
            size_value="0.123456789012345678900",
        )
    )
    result = adapter.extract(IMAGE, media_type="image/png")
    assert result.candidate.sku == "000012"
    assert result.candidate.barcode == "0000123456789"
    assert result.candidate.size_value.as_tuple() == Decimal("0.123456789012345678900").as_tuple()


@pytest.mark.parametrize(
    "content",
    [
        "{",
        chr(96) * 3 + 'json\n{"name":"Tea"}\n' + chr(96) * 3,
        '{"name":"Tea","name":"Coffee","confidence":0.5}',
        '{"name":"Tea","confidence":NaN}',
    ],
)
def test_malformed_json(content):
    adapter, _ = extractor(content)
    with pytest.raises(MalformedJSONError):
        adapter.extract(IMAGE, media_type="image/png")


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"confidence": "0.8"},
        {"confidence": True},
        {"uncertain_fields": ["price"]},
        {"uncertain_fields": ["barcode", "barcode"]},
        {"barcode": 123},
        {"sku": 123},
        {"size_value": 0.1},
        {"size_value": "NaN"},
        {"size_value": "-2"},
        {"size_value": "0"},
        {"extra": "not allowed"},
        {"name": 123},
        {"brand": "Harbor", "uncertain_fields": ["brand"]},
    ],
)
def test_schema_failure(changes):
    adapter, _ = extractor(valid(**changes))
    with pytest.raises(ExtractionSchemaError):
        adapter.extract(IMAGE, media_type="image/png")


@pytest.mark.parametrize("content", ["[]", "null", '{"name":"Tea"}'])
def test_wrong_top_level_or_missing_confidence(content):
    adapter, _ = extractor(content)
    with pytest.raises(ExtractionSchemaError):
        adapter.extract(IMAGE, media_type="image/png")


@pytest.mark.parametrize("name", [None, "", "  ", "unknown", "Unknown Product", "n/a"])
def test_missing_product_name(name):
    adapter, _ = extractor(valid(name=name))
    with pytest.raises(MissingProductNameError):
        adapter.extract(IMAGE, media_type="image/png")


def test_blank_optional_field_remains_unknown():
    adapter, _ = extractor(valid(barcode="   ", sku=""))
    result = adapter.extract(IMAGE, media_type="image/png")
    assert result.candidate.barcode is None
    assert result.candidate.sku is None


@pytest.mark.parametrize(
    "error,expected",
    [
        (ConnectionError("secret provider detail"), OllamaUnavailableError),
        (httpx.ConnectError("secret provider detail"), OllamaUnavailableError),
        (httpx.ReadTimeout("secret provider detail"), OllamaTimeoutError),
        (ResponseError("secret provider detail", status_code=404), ProviderFailureError),
        (httpx.RemoteProtocolError("secret provider detail"), ProviderFailureError),
        (RuntimeError("secret provider detail"), ProviderFailureError),
    ],
)
def test_provider_errors_are_safe(error, expected):
    adapter, _ = extractor(error=error)
    with pytest.raises(expected) as caught:
        adapter.extract(IMAGE, media_type="image/png")
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "content,done,reason",
    [
        ("", True, "stop"),
        (None, True, "stop"),
        (" ", True, "stop"),
        (valid(), False, "stop"),
        (valid(), True, "length"),
    ],
)
def test_empty_or_incomplete_response(content, done, reason):
    adapter, _ = extractor(content, done=done, reason=reason)
    with pytest.raises(InvalidModelResponseError):
        adapter.extract(IMAGE, media_type="image/png")


@pytest.mark.parametrize(
    "image,mime,expected",
    [
        (IMAGE, "image/gif", UnsupportedImageTypeError),
        (b"x" * 1001, "image/png", OversizedImageError),
        (b"", "image/png", InvalidImageError),
    ],
)
def test_invalid_input_never_calls_provider(image, mime, expected):
    adapter, client = extractor(valid())
    with pytest.raises(expected):
        adapter.extract(image, media_type=mime)
    client.chat.assert_not_called()


def test_exact_size_limit_is_allowed():
    adapter, _ = extractor(valid())
    assert adapter.extract(b"x" * 1000, media_type="image/png").candidate.name == "Oat Drink"


def test_configuration_is_passed_to_sdk():
    settings = Settings(
        _env_file=None,
        ollama_model="another-vision-model",
        ollama_base_url="http://localhost:12345",
        ollama_timeout_seconds=17,
        max_image_bytes=10,
    )
    with patch("stockentry_guard.infrastructure.ai.ollama_extractor.Client") as client:
        adapter = build_product_extractor(settings)
        client.assert_called_once_with(host="http://localhost:12345", timeout=17)
        with pytest.raises(OversizedImageError):
            adapter.extract(b"x" * 11, media_type="image/png")
        client.return_value.chat.assert_not_called()


def test_sdk_receives_base64_image_without_network():
    def handle(request):
        payload = json.loads(request.content)
        assert payload["messages"][1]["images"] == ["ZmFrZS1pbWFnZS1ieXRlcw=="]
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"role": "assistant", "content": valid()},
            },
        )

    client = Client(host="http://test.invalid", transport=httpx.MockTransport(handle))
    adapter = OllamaProductExtractor(
        base_url="http://test.invalid",
        model_name="test-model",
        timeout_seconds=1,
        max_image_bytes=1000,
        client=client,
    )
    assert adapter.extract(IMAGE, media_type="image/png").candidate.name == "Oat Drink"


def test_event_contains_only_controlled_metadata():
    adapter, _ = extractor(valid(description="must never be logged"))
    result = adapter.extract(IMAGE, media_type="image/png")
    correlation_id = uuid4()
    event = product_extracted_event(result, correlation_id)
    payload = validated_payload(event)
    assert event.schema_version == 2
    assert event.product_id is None
    assert event.correlation_id == correlation_id
    assert set(payload) == {
        "model_name",
        "prompt_version",
        "schema_version",
        "confidence",
        "uncertain_field_count",
        "processing_ms",
    }
    assert "must never be logged" not in str(payload)


def test_version_two_event_can_be_persisted_by_caller(sessions):
    from stockentry_guard.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork

    adapter, _ = extractor(valid())
    event = product_extracted_event(adapter.extract(IMAGE, media_type="image/png"), uuid4())
    with SQLAlchemyUnitOfWork(sessions) as uow:
        uow.events.append(event)
        uow.commit()
    with SQLAlchemyUnitOfWork(sessions) as uow:
        assert uow.events.list_for_workflow(event.correlation_id) == [event]
        assert uow.products.list_all() == []


REQUIRED_RESPONSE_FIELDS = {
    "name",
    "sku",
    "barcode",
    "brand",
    "category",
    "variant",
    "size_value",
    "size_unit",
    "description",
    "confidence",
    "uncertain_fields",
}


def test_generated_schema_requires_every_response_key():
    schema = ExtractionResponseSchema.model_json_schema()
    assert set(schema["required"]) == REQUIRED_RESPONSE_FIELDS
    for field in REQUIRED_RESPONSE_FIELDS - {"confidence", "uncertain_fields"}:
        assert {"type": "null"} in schema["properties"][field]["anyOf"]
        assert "default" not in schema["properties"][field]


def test_complete_response_with_null_product_fields_parses():
    parsed = parse_extraction_response(valid(name=None, uncertain_fields=["name"]))
    assert parsed.name is None
    assert parsed.sku is None
    assert parsed.barcode is None
    assert parsed.brand is None
    assert parsed.category is None
    assert parsed.variant is None
    assert parsed.size_value is None
    assert parsed.size_unit is None
    assert parsed.description is None
    assert parsed.confidence == 0.8
    assert parsed.uncertain_fields == ["name"]
    with pytest.raises(MissingProductNameError):
        parsed.to_candidate()


@pytest.mark.parametrize("field", sorted(REQUIRED_RESPONSE_FIELDS))
def test_missing_response_key_is_rejected(field):
    payload = json.loads(valid())
    del payload[field]
    with pytest.raises(ExtractionSchemaError):
        parse_extraction_response(json.dumps(payload))


def test_metadata_only_response_is_rejected():
    with pytest.raises(ExtractionSchemaError):
        parse_extraction_response('{"confidence": 0.95, "uncertain_fields": ["name"]}')
