"""Explicit dependency wiring. Importing this module performs no I/O."""

from collections.abc import Callable

from sqlalchemy.orm import sessionmaker

from stockentry_guard.application.use_cases.check_duplicates import CheckDuplicates
from stockentry_guard.config import Settings
from stockentry_guard.domain.duplicates import DuplicateThresholds
from stockentry_guard.infrastructure.ai.ollama_extractor import OllamaProductExtractor
from stockentry_guard.infrastructure.persistence.database import create_database_engine
from stockentry_guard.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork
from stockentry_guard.infrastructure.similarity.deterministic import DeterministicSimilarityEngine
from stockentry_guard.ports.services import AIProductExtractor
from stockentry_guard.ports.unit_of_work import UnitOfWork


def build_unit_of_work_factory(settings: Settings) -> Callable[[], UnitOfWork]:
    """Create an engine and transaction factory; never create or migrate tables."""
    engine = create_database_engine(settings.database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    return lambda: SQLAlchemyUnitOfWork(sessions)


def build_product_extractor(settings: Settings) -> AIProductExtractor:
    return OllamaProductExtractor(
        base_url=settings.ollama_base_url,
        model_name=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_image_bytes=settings.max_image_bytes,
    )


def build_duplicate_checker(settings: Settings) -> CheckDuplicates:
    return CheckDuplicates(
        DeterministicSimilarityEngine(),
        DuplicateThresholds(
            review=settings.duplicate_review_threshold,
            likely=settings.duplicate_likely_threshold,
        ),
    )
