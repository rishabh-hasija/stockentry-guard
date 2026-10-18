"""Extract outside a transaction, then persist only controlled success metadata."""

from collections.abc import Callable
from uuid import UUID

from stockentry_guard.application.extraction_events import product_extracted_event
from stockentry_guard.ports.extraction import ExtractionResult
from stockentry_guard.ports.services import AIProductExtractor
from stockentry_guard.ports.unit_of_work import UnitOfWork


class ExtractProductFromImage:
    def __init__(
        self,
        extractor: AIProductExtractor,
        uow_factory: Callable[[], UnitOfWork],
    ) -> None:
        self._extractor = extractor
        self._uow_factory = uow_factory

    def execute(self, image: bytes, *, media_type: str, correlation_id: UUID) -> ExtractionResult:
        result = self._extractor.extract(image, media_type=media_type)
        with self._uow_factory() as uow:
            uow.events.append(product_extracted_event(result, correlation_id))
            uow.commit()
        return result
