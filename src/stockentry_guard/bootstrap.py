"""Explicit dependency wiring. Importing this module performs no I/O."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from stockentry_guard.application.use_cases.approve_product import (
    ApproveNewProduct,
    UpdateExistingProduct,
)
from stockentry_guard.application.use_cases.check_duplicates import CheckDuplicates
from stockentry_guard.application.use_cases.check_product_duplicates import CheckProductDuplicates
from stockentry_guard.application.use_cases.extract_product import ExtractProductFromImage
from stockentry_guard.application.use_cases.inventory import ExportInventory, ImportInventory
from stockentry_guard.application.use_cases.reject_candidate import RejectCandidate
from stockentry_guard.config import Settings
from stockentry_guard.domain.duplicates import DuplicateThresholds
from stockentry_guard.infrastructure.ai.ollama_extractor import OllamaProductExtractor
from stockentry_guard.infrastructure.inventory.csv_adapter import (
    CSVInventoryExporter,
    CSVInventoryImporter,
)
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


@dataclass(frozen=True, slots=True)
class ApplicationWorkflows:
    import_inventory: ImportInventory
    extract_product_from_image: ExtractProductFromImage
    check_product_duplicates: CheckProductDuplicates
    approve_new_product: ApproveNewProduct
    update_existing_product: UpdateExistingProduct
    reject_candidate: RejectCandidate
    export_inventory: ExportInventory


def build_workflows(settings: Settings) -> ApplicationWorkflows:
    """Wire the application slice without running inference or migrations."""
    factory = build_unit_of_work_factory(settings)
    checker = CheckProductDuplicates(
        build_duplicate_checker(settings),
        factory,
        engine_version="deterministic-v1",
        threshold_version=(
            f"review={settings.duplicate_review_threshold:g};"
            f"likely={settings.duplicate_likely_threshold:g}"
        ),
    )
    return ApplicationWorkflows(
        import_inventory=ImportInventory(CSVInventoryImporter(), factory),
        extract_product_from_image=ExtractProductFromImage(
            build_product_extractor(settings), factory
        ),
        check_product_duplicates=checker,
        approve_new_product=ApproveNewProduct(checker, factory),
        update_existing_product=UpdateExistingProduct(checker, factory),
        reject_candidate=RejectCandidate(factory),
        export_inventory=ExportInventory(CSVInventoryExporter(), factory),
    )
