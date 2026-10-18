"""Streamlit presentation for the existing inventory onboarding workflows."""

import csv
import io
import logging
from collections.abc import MutableMapping
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

import streamlit as st

from stockentry_guard.bootstrap import ApplicationWorkflows, build_workflows
from stockentry_guard.config import Settings
from stockentry_guard.domain.duplicates import DuplicateResult
from stockentry_guard.domain.errors import ConcurrentProductUpdateError
from stockentry_guard.domain.events import EventType, StoredEvent
from stockentry_guard.domain.models import (
    Classification,
    Product,
    ProductCandidate,
    SimilarityMatch,
)
from stockentry_guard.ports.extraction import ExtractionError, ExtractionResult
from stockentry_guard.ports.inventory import InventoryImportError
from stockentry_guard.ui.state import (
    FIELDS,
    checked_for_current,
    clear_review,
    install_extraction,
    invalidate_review,
    reviewed_candidate,
    sync_image,
    sync_review,
)

LOGGER = logging.getLogger(__name__)
IMAGE_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
SAMPLE_CSV_FILENAME = "stockentry_sample_inventory.csv"
SAMPLE_CSV_COLUMNS = (
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
SAMPLE_CSV_ROWS = (
    (
        "001",
        "0001234567890",
        "Coca-Cola Zero",
        "Coca-Cola",
        "Soft Drink",
        "Zero Sugar",
        "330",
        "ml",
        "Sugar-free cola drink",
    ),
    (
        "002",
        "0009876543210",
        "Nutella",
        "Ferrero",
        "Spread",
        "Hazelnut",
        "400",
        "g",
        "Hazelnut cocoa spread",
    ),
    (
        "003",
        "",
        "Nivea Soft Cream",
        "Nivea",
        "Personal Care",
        "Soft",
        "100",
        "ml",
        "Moisturizing cream",
    ),
)
LABELS = {
    "name": "Product name",
    "sku": "SKU",
    "barcode": "Barcode",
    "brand": "Brand",
    "category": "Category",
    "variant": "Variant",
    "size_value": "Size value",
    "size_unit": "Size unit",
    "description": "Description",
}

CLASSIFICATION_LABELS = {
    Classification.NEW_PRODUCT: "No matching product found",
    Classification.NEEDS_REVIEW: "Possible match found",
    Classification.LIKELY_DUPLICATE: "Likely already in inventory",
}

ACTIVITY_LABELS = {
    EventType.INVENTORY_IMPORTED: "Inventory imported",
    EventType.PRODUCT_EXTRACTED: "Product details extracted",
    EventType.DUPLICATE_CHECKED: "Duplicate check completed",
    EventType.DUPLICATE_FOUND: "Possible duplicate found",
    EventType.PRODUCT_APPROVED: "Product approved",
    EventType.PRODUCT_CREATED: "Product added",
    EventType.PRODUCT_UPDATED: "Product updated",
    EventType.PRODUCT_REJECTED: "Product discarded",
}


def classification_label(classification: Classification) -> str:
    return CLASSIFICATION_LABELS[classification]


def match_reasons(match: SimilarityMatch) -> tuple[str, ...]:
    """Translate available comparison evidence without exposing internal codes."""
    signals = {signal.name: signal.score for signal in match.signals}
    reasons: list[str] = []
    if signals.get("brand") == 1:
        reasons.append("Same brand")
    name_score = signals.get("name")
    if name_score is not None and name_score >= 0.6:
        reasons.append("Similar product name")
    if "SIZE_CONFLICT" in match.blockers or signals.get("size") == 0:
        reasons.append("Different size")
    elif signals.get("size") == 1:
        reasons.append("Same size")
    if "VARIANT_CONFLICT" in match.blockers or signals.get("variant") == 0:
        reasons.append("Different variant")
    elif signals.get("variant") == 1:
        reasons.append("Same variant")
    if signals.get("barcode") == 1:
        reasons.append("Same barcode")
    elif "BARCODE_CONFLICT" in match.blockers:
        reasons.append("Different barcode")
    if signals.get("sku") == 1:
        reasons.append("Same SKU")
    return tuple(reasons) or ("No clear matching details",)


def activity_label(event_type: EventType) -> str:
    return ACTIVITY_LABELS[event_type]


def activity_summary(item: StoredEvent) -> str:
    event = item.event
    payload = event.payload
    if event.event_type == EventType.INVENTORY_IMPORTED:
        return f"Imported {payload.get('accepted_count', '?')} products"
    if event.event_type == EventType.PRODUCT_EXTRACTED:
        return f"Extracted with {payload.get('model_name', payload.get('model_tag', '?'))}"
    if event.event_type == EventType.DUPLICATE_CHECKED:
        classification = payload.get("classification")
        label = next(
            (
                friendly
                for value, friendly in CLASSIFICATION_LABELS.items()
                if value == classification
            ),
            "Check completed",
        )
        return label
    if event.event_type == EventType.DUPLICATE_FOUND:
        score = payload.get("score")
        return f"Match score: {score:.0%}" if isinstance(score, float) else "Match found"
    if event.event_type == EventType.PRODUCT_APPROVED:
        return "Create approved" if payload.get("action") == "create" else "Update approved"
    if event.event_type == EventType.PRODUCT_CREATED:
        return "Added to inventory"
    if event.event_type == EventType.PRODUCT_UPDATED:
        return "Changes saved"
    return "No inventory change"


@st.cache_resource(show_spinner=False)
def workflows() -> ApplicationWorkflows:
    return build_workflows(Settings())


def show_failure(message: str) -> None:
    LOGGER.exception("Streamlit workflow failed")
    st.error(message)


def product_label(product: Product) -> str:
    size = (
        "" if product.size_value is None else f" · {product.size_value} {product.size_unit or ''}"
    )
    return f"{product.name}{size} · {str(product.id)[:8]} · rev {product.revision}"


def sample_inventory_csv() -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(SAMPLE_CSV_COLUMNS)
    writer.writerows(SAMPLE_CSV_ROWS)
    return output.getvalue().encode("utf-8")


def render_inventory(services: ApplicationWorkflows, products: tuple[Product, ...]) -> None:
    st.header("Inventory")
    st.metric("Products", len(products))
    st.download_button(
        "Download sample CSV",
        sample_inventory_csv(),
        file_name=SAMPLE_CSV_FILENAME,
        mime="text/csv",
        key="sample_inventory",
    )
    upload_key = f"inventory_csv_{st.session_state.setdefault('import_upload_nonce', uuid4())}"
    uploaded = st.file_uploader("Import inventory CSV", type=["csv"], key=upload_key)
    if st.button("Import CSV", disabled=uploaded is None):
        assert uploaded is not None
        try:
            result = services.import_inventory.execute(
                uploaded.getvalue(), correlation_id=uuid4(), approved=True
            )
        except InventoryImportError as exc:
            st.error("Import rejected. No products were added.")
            st.dataframe(
                [
                    {"Row": e.row, "Field": e.field, "Code": e.code, "Problem": e.message}
                    for e in exc.errors
                ],
                hide_index=True,
            )
        except Exception:
            show_failure("Import failed. Check the database and try again.")
        else:
            st.session_state["notice"] = f"Imported {result.accepted_count} products."
            st.session_state["import_upload_nonce"] = uuid4()
            st.rerun()
    if products:
        st.dataframe(
            [
                {
                    "Name": p.name,
                    "Brand": p.brand or "",
                    "Variant": p.variant or "",
                    "Size": "" if p.size_value is None else str(p.size_value),
                    "Unit": p.size_unit or "",
                    "SKU": p.sku or "",
                    "Barcode": p.barcode or "",
                    "Category": p.category or "",
                }
                for p in products
            ],
            hide_index=True,
        )
    else:
        st.info("Inventory is empty. Import a CSV or approve a new product.")
    try:
        content = services.export_inventory.execute()
    except Exception:
        show_failure("CSV export is unavailable right now.")
    else:
        st.download_button(
            "Export inventory CSV",
            content,
            file_name="stockentry_inventory.csv",
            mime="text/csv",
            key="export_inventory",
        )


def render_matches(
    result: DuplicateResult,
    candidate: ProductCandidate,
    products: tuple[Product, ...],
) -> None:
    st.subheader("Duplicate Review")
    if result.classification == Classification.LIKELY_DUPLICATE:
        st.error(classification_label(result.classification))
    elif result.classification == Classification.NEEDS_REVIEW:
        st.warning(classification_label(result.classification))
    else:
        st.success(classification_label(result.classification))
    by_id = {product.id: product for product in products}
    if not products:
        st.info("There are no existing products to compare yet.")
        return
    if not result.matches:
        st.info("No product comparisons are available.")
        return
    st.write(f"Match score: {result.top_score:.0%}")
    st.dataframe(
        [
            {
                "Product": by_id[m.product_id].name if m.product_id in by_id else str(m.product_id),
                "Match score": f"{m.score:.0%}",
                "Why": ", ".join(match_reasons(m)),
            }
            for m in result.matches[:5]
        ],
        hide_index=True,
    )
    for match in result.matches[:5]:
        product = by_id.get(match.product_id)
        if product is None:
            continue
        with st.expander(f"Compare with {product_label(product)} · {match.score:.0%} match"):
            st.dataframe(
                [
                    {
                        "Field": LABELS[field],
                        "Reviewed": str(getattr(candidate, field) or ""),
                        "Existing": str(getattr(product, field) or ""),
                    }
                    for field in FIELDS
                ],
                hide_index=True,
            )
            st.write("Comparison details")
            st.dataframe(
                [
                    {
                        "Detail": (
                            "Size" if s.name == "size" else LABELS.get(s.name, "Product detail")
                        ),
                        "Match": "-" if s.score is None else f"{s.score:.0%}",
                    }
                    for s in match.signals
                ],
                hide_index=True,
            )


def render_approval(
    services: ApplicationWorkflows,
    candidate: ProductCandidate,
    result: DuplicateResult | None,
    products: tuple[Product, ...],
) -> None:
    st.subheader("Approval")
    state = cast(MutableMapping[str, object], st.session_state)
    by_id = {product.id: product for product in products}
    options: list[UUID | None] = [None, *by_id]
    if state.get("selected_target") not in options:
        state.pop("selected_target", None)
    selected = st.selectbox(
        "Existing product to update",
        options,
        format_func=lambda value: (
            "Choose a product" if value is None else product_label(by_id[value])
        ),
        key="selected_target",
    )
    target = by_id.get(selected) if selected is not None else None
    sync_review(state, candidate, None if target is None else (target.id, target.revision))
    ready = checked_for_current(state, candidate)
    if not ready:
        st.info("Check duplicates for the current reviewed fields before approving.")
    if target is not None:
        st.warning(f"Updating {product_label(target)} replaces its reviewed product fields.")
    create_disabled = not ready or (
        result is not None and result.classification == Classification.LIKELY_DUPLICATE
    )
    create_col, update_col, reject_col = st.columns(3)
    with create_col:
        create_clicked = st.button("Create new product", type="primary", disabled=create_disabled)
    with update_col:
        update_clicked = st.button("Update selected product", disabled=not ready or target is None)
    with reject_col:
        reject_clicked = st.button("Discard product")
    if create_clicked or update_clicked:
        try:
            if create_clicked:
                services.approve_new_product.execute(
                    candidate,
                    approved=True,
                    command_id=cast(str, state["create_command_id"]),
                    correlation_id=cast(UUID, state["workflow_id"]),
                )
                success_message = "Product added successfully."
            else:
                assert target is not None
                services.update_existing_product.execute(
                    candidate,
                    target_id=target.id,
                    expected_revision=target.revision,
                    approved=True,
                    command_id=cast(str, state["update_command_id"]),
                    correlation_id=cast(UUID, state["workflow_id"]),
                )
                success_message = "Product updated successfully."
        except ConcurrentProductUpdateError:
            st.error("This product changed since it was loaded. Refresh and review it again.")
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            show_failure("Approval failed. Inventory was not confirmed as changed.")
        else:
            state["notice"] = success_message
            state["pending_clear_review"] = True
            st.rerun()
    if reject_clicked:
        try:
            services.reject_candidate.execute(
                correlation_id=cast(UUID, state["workflow_id"]), reason_code="USER_REJECTED"
            )
        except Exception:
            show_failure("Could not record the rejection. Try again.")
        else:
            state["notice"] = "Candidate rejected. Inventory was not changed."
            state["pending_clear_review"] = True
            st.rerun()


def render_add_product(services: ApplicationWorkflows, products: tuple[Product, ...]) -> None:
    st.header("Add Product")
    state = cast(MutableMapping[str, object], st.session_state)
    image = st.file_uploader(
        "Product image (JPEG, PNG, or WebP)",
        type=["jpg", "jpeg", "png", "webp"],
        key="product_image",
    )
    image_bytes = None if image is None else image.getvalue()
    if state.pop("pending_clear_review", False):
        clear_review(state)
    sync_image(state, None if image_bytes is None else sha256(image_bytes).hexdigest())
    if image is not None and image_bytes is not None:
        st.image(image_bytes, caption=image.name)
    if st.button("Extract product details", disabled=image_bytes is None):
        assert image is not None and image_bytes is not None
        clear_review(state)
        state["workflow_id"] = uuid4()
        media_type = IMAGE_TYPES.get(image.name.rsplit(".", 1)[-1].lower(), image.type)
        try:
            with st.spinner("Extracting product details locally…"):
                extraction_result = services.extract_product_from_image.execute(
                    image_bytes,
                    media_type=media_type,
                    correlation_id=cast(UUID, state["workflow_id"]),
                )
        except ExtractionError as exc:
            st.error(f"Extraction could not complete: {type(exc).__name__}. Try another image.")
        except Exception:
            show_failure("Extraction failed. Check the local model and try again.")
        else:
            install_extraction(state, extraction_result)
            st.success("Extraction complete. Review every field before approval.")
    extraction = state.get("extraction")
    if not isinstance(extraction, ExtractionResult):
        st.info("Upload an image and run extraction to start a product review.")
        return
    metadata = extraction.metadata
    st.subheader("Review extracted fields")
    st.write(f"Model: {metadata.model_name} · Prompt: {metadata.prompt_version}")
    st.write(f"Model-reported confidence: {metadata.confidence:.2f}")
    st.write("Uncertain fields: " + (", ".join(metadata.uncertain_fields) or "None reported"))
    left, right = st.columns(2)
    values: dict[str, str] = {}
    for index, field in enumerate(FIELDS):
        with left if index % 2 == 0 else right:
            values[field] = (
                st.text_area(LABELS[field], key=f"edit_{field}")
                if field == "description"
                else st.text_input(LABELS[field], key=f"edit_{field}")
            )
    try:
        candidate = reviewed_candidate(values)
    except ValueError as exc:
        invalidate_review(state)
        st.warning(f"Review needed: {exc}")
        st.subheader("Duplicate Review")
        st.info("Complete valid product fields before checking duplicates.")
        st.subheader("Approval")
        st.info("Approval is disabled until the fields are valid and duplicates are checked.")
        return
    sync_review(state, candidate, cast(tuple[UUID, int] | None, state.get("update_target")))
    if st.button("Check duplicates"):
        try:
            checked_result = services.check_product_duplicates.execute(
                candidate, correlation_id=cast(UUID, state["workflow_id"])
            )
        except Exception:
            show_failure("Duplicate check failed. Try again before approving.")
        else:
            state["duplicate_result"] = checked_result
            state["checked_candidate"] = candidate
    result = state.get("duplicate_result")
    active_result = (
        result
        if isinstance(result, DuplicateResult) and checked_for_current(state, candidate)
        else None
    )
    if active_result is not None:
        render_matches(active_result, candidate, products)
    else:
        st.subheader("Duplicate Review")
        st.info("Run duplicate check after reviewing or editing the fields.")
    render_approval(services, candidate, active_result, products)


def render_activity(services: ApplicationWorkflows) -> None:
    st.header("Recent Activity")
    try:
        items = services.recent_activity.execute(limit=20)
    except Exception:
        show_failure("Recent activity is unavailable right now.")
        return
    if not items:
        st.info("No workflow actions recorded yet.")
        return
    st.dataframe(
        [
            {
                "Time": item.event.occurred_at.strftime("%d %b %Y, %H:%M UTC"),
                "Action": activity_label(item.event.event_type),
                "Product": str(item.event.product_id) if item.event.product_id else "-",
                "Details": activity_summary(item),
            }
            for item in items
        ],
        hide_index=True,
    )


def main() -> None:
    st.set_page_config(page_title="StockEntry Guard", layout="wide")
    st.title("StockEntry Guard")
    st.caption("Local AI-assisted product onboarding for small retailers")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    try:
        services = workflows()
        products = services.list_inventory.execute()
    except Exception:
        show_failure("Inventory is unavailable. Run 'python -m alembic upgrade head' and restart.")
        st.stop()
    inventory_tab, add_tab, activity_tab = st.tabs(["Inventory", "Add Product", "Recent Activity"])
    with inventory_tab:
        render_inventory(services, products)
    with add_tab:
        render_add_product(services, products)
    with activity_tab:
        render_activity(services)


if __name__ == "__main__":
    main()
