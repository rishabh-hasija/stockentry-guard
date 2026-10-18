"""Structured import failures independent of CSV and persistence details."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImportRowError:
    row: int
    field: str
    code: str
    message: str


class InventoryImportError(ValueError):
    def __init__(self, errors: tuple[ImportRowError, ...]) -> None:
        self.errors = errors
        super().__init__(f"Inventory import failed with {len(errors)} error(s)")
