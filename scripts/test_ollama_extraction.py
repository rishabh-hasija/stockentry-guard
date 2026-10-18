"""Manual opt-in extraction: no image, candidate, or event is persisted."""

import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from stockentry_guard.bootstrap import build_product_extractor
from stockentry_guard.config import Settings
from stockentry_guard.ports.extraction import ExtractionError, OversizedImageError


def json_value(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--media-type", help="Override media type inferred from the file extension")
    args = parser.parse_args()
    settings = Settings()
    media_type = args.media_type or {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(args.image.suffix.lower(), "application/octet-stream")
    try:
        with args.image.open("rb") as source:
            image = source.read(settings.max_image_bytes + 1)
        if len(image) > settings.max_image_bytes:
            raise OversizedImageError("Image exceeds configured byte limit")
        result = build_product_extractor(settings).extract(image, media_type=media_type)
    except (ExtractionError, OSError) as error:
        print(f"Extraction failed ({type(error).__name__}): {error}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), default=json_value, indent=2, ensure_ascii=False))
    print("Candidate only; human review required. No inventory or events written.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
