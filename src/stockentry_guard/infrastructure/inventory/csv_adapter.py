"""UTF-8 CSV interchange. All parsing/validation precedes inventory writes."""

import csv
import io
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from stockentry_guard.application.review import ReviewedProductSchema
from stockentry_guard.domain.models import Product, ProductCandidate
from stockentry_guard.domain.normalization import normalize_text
from stockentry_guard.ports.inventory import ImportRowError, InventoryImportError

COLUMNS = (
    "sku",
    "barcode",
    "name",
    "brand",
    "category",
    "variant",
    "size_value",
    "size_unit",
    "description",
)


class CSVInventoryImporter:
    def parse(self, content: bytes) -> Sequence[ProductCandidate]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise InventoryImportError(
                (ImportRowError(0, "", "encoding", "Input must be UTF-8"),)
            ) from None
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            headers = next(reader, [])
        except csv.Error:
            raise InventoryImportError(
                (ImportRowError(1, "", "csv_syntax", "Malformed CSV header"),)
            ) from None
        header_errors: list[ImportRowError] = []
        if "name" not in headers:
            header_errors.append(
                ImportRowError(1, "name", "missing_column", "Name column required")
            )
        if len(headers) != len(set(headers)):
            header_errors.append(ImportRowError(1, "", "duplicate_column", "Repeated column name"))
        for column in headers:
            if column not in COLUMNS:
                header_errors.append(
                    ImportRowError(1, column, "unknown_column", "Unsupported column")
                )
        if header_errors:
            raise InventoryImportError(tuple(header_errors))
        errors: list[ImportRowError] = []
        candidates: list[ProductCandidate] = []
        seen: dict[tuple[str, ...], int] = {}
        identifiers: dict[tuple[str, str], int] = {}
        try:
            for row in reader:
                line = reader.line_num
                if not row:  # Physically empty lines are ignored, not blank product records.
                    continue
                if len(row) != len(headers):
                    errors.append(ImportRowError(line, "", "column_count", "Wrong number of cells"))
                    continue
                data: dict[str, object] = dict(zip(headers, row, strict=True))
                raw_size = data.get("size_value")
                if isinstance(raw_size, str) and raw_size.strip():
                    try:
                        data["size_value"] = Decimal(raw_size)
                    except InvalidOperation:
                        errors.append(
                            ImportRowError(
                                line,
                                "size_value",
                                "invalid_decimal",
                                "Size must be a positive Decimal",
                            )
                        )
                        continue
                else:
                    data["size_value"] = None
                try:
                    reviewed = ReviewedProductSchema.model_validate(data)
                except ValidationError as error:
                    for issue in error.errors(include_input=False, include_url=False):
                        errors.append(
                            ImportRowError(
                                line,
                                str(issue["loc"][0]),
                                str(issue["type"]),
                                str(issue["msg"]),
                            )
                        )
                    continue
                candidate = ProductCandidate(**reviewed.model_dump())
                # Conservative conflicts, not a fuzzy merge or a universal SKU identity rule.
                values = reviewed.model_dump()
                key = tuple(
                    str(values[column] or "")
                    if column in {"sku", "barcode", "size_value"}
                    else normalize_text(values[column])
                    for column in COLUMNS
                )
                if key in seen:
                    errors.append(
                        ImportRowError(
                            line,
                            "",
                            "duplicate_row",
                            f"Duplicates row {seen[key]}",
                        )
                    )
                seen.setdefault(key, line)
                for field in ("sku", "barcode"):
                    value = getattr(candidate, field)
                    if value:
                        identifier = (field, value)
                        if identifier in identifiers:
                            errors.append(
                                ImportRowError(
                                    line,
                                    field,
                                    "identifier_conflict",
                                    f"Identifier already occurs on row {identifiers[identifier]}",
                                )
                            )
                        identifiers.setdefault(identifier, line)
                candidates.append(candidate)
        except csv.Error:
            errors.append(ImportRowError(reader.line_num, "", "csv_syntax", "Malformed CSV record"))
        if not candidates and not errors:
            errors.append(ImportRowError(2, "", "empty_inventory", "No product rows supplied"))
        if errors:
            raise InventoryImportError(tuple(errors))
        return tuple(candidates)


def spreadsheet_safe(value: str) -> str:
    """Protect the output only; do not strip or modify stored values."""
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class CSVInventoryExporter:
    def serialize(self, products: Sequence[Product]) -> bytes:
        target = io.StringIO(newline="")
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(COLUMNS)
        for product in sorted(products, key=lambda item: str(item.id)):
            writer.writerow(
                [
                    spreadsheet_safe(str(value)) if value is not None else ""
                    for value in (getattr(product, column) for column in COLUMNS)
                ]
            )
        return target.getvalue().encode("utf-8")
