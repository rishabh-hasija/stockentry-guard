"""Pure comparison views. Original candidate/product values are never modified."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from stockentry_guard.domain.models import Product, ProductCandidate

_UNITS = {
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "gramme": "g",
    "grammes": "g",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilogramme": "kg",
    "kilogrammes": "kg",
}
_SIZE = re.compile(
    r"(?<![\w.,])(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>"
    + "|".join(sorted(_UNITS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def normalize_unit(unit: str | None) -> str | None:
    return _UNITS.get(normalize_text(unit))


@dataclass(frozen=True, slots=True)
class ComparisonSize:
    value: Decimal
    unit: str


def normalize_size(value: Decimal | None, unit: str | None) -> ComparisonSize | None:
    canonical = normalize_unit(unit)
    if value is None or canonical is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        return None
    if canonical in {"l", "kg"}:
        parts = value.as_tuple()
        # Finite Decimal exponent is an int; construct rather than round via arithmetic.
        assert isinstance(parts.exponent, int)
        value = Decimal((parts.sign, parts.digits, parts.exponent + 3))
        canonical = {"l": "ml", "kg": "g"}[canonical]
    return ComparisonSize(value, canonical)


def parse_size(text: str) -> ComparisonSize | None:
    """Parse a whole measurement such as 330 ML or 0.33 litres."""
    match = _SIZE.fullmatch(unicodedata.normalize("NFKC", text).strip())
    if match is None:
        return None
    return normalize_size(
        Decimal(match["value"].replace(",", ".")),
        match["unit"],
    )


def normalize_variant(value: str | None) -> str:
    text = normalize_text(value)
    return {"zero sugar": "zero", "sugar free": "sugarfree"}.get(text, text)


@dataclass(frozen=True, slots=True)
class ComparisonProduct:
    name: str
    brand: str
    variant: str
    category: str
    size: ComparisonSize | None
    barcode: str | None
    sku: str | None
    issues: tuple[str, ...]


def comparison_view(product: Product | ProductCandidate) -> ComparisonProduct:
    raw_name = unicodedata.normalize("NFKC", product.name or "")
    measurements = list(_SIZE.finditer(raw_name))
    name_sizes = [parse_size(m.group()) for m in measurements]
    issues: list[str] = []
    size = normalize_size(product.size_value, product.size_unit)
    if (product.size_value is not None or product.size_unit is not None) and size is None:
        issues.append("UNRESOLVED_SIZE")
    multipack = re.search(r"\b\d+\s*[x×]\s*\d", raw_name, re.IGNORECASE)
    if len(name_sizes) > 1 or multipack:
        issues.append("AMBIGUOUS_SIZE")
    elif len(name_sizes) == 1:
        if size is None and not issues:
            size = name_sizes[0]
        elif size is not None and size != name_sizes[0]:
            issues.append("SIZE_EVIDENCE_CONFLICT")
    name = normalize_text(_SIZE.sub(" ", raw_name))
    variant = normalize_variant(product.variant)
    # Limited visible-name fallback, not a model or inference of hidden attributes.
    visible = normalize_variant(name)
    visible = re.sub(r"\bzero sugar\b", "zero", visible)
    visible = re.sub(r"\bsugar free\b", "sugarfree", visible)
    markers = sorted(set(visible.split()) & {"original", "zero", "sugarfree", "diet", "max"})
    inferred = " ".join(markers)
    if not variant:
        variant = inferred
    elif inferred and variant != inferred:
        issues.append("VARIANT_EVIDENCE_CONFLICT")
    return ComparisonProduct(
        name=name,
        brand=normalize_text(product.brand),
        variant=variant,
        category=normalize_text(product.category),
        size=size,
        # Identifiers stay case-sensitive strings; punctuation/zeroes are not removed.
        barcode=product.barcode if product.barcode and product.barcode.strip() else None,
        sku=product.sku if product.sku and product.sku.strip() else None,
        issues=tuple(issues),
    )
