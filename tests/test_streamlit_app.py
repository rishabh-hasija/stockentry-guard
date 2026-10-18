"""Render the MVP page against isolated SQLite without Ollama or a browser."""

import csv
import io
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import streamlit as st
from sqlalchemy import Engine
from streamlit.testing.v1 import AppTest

from stockentry_guard.domain.models import ProductCandidate
from stockentry_guard.ports.extraction import ExtractionMetadata, ExtractionResult
from stockentry_guard.ui.state import FIELDS
from stockentry_guard.ui.streamlit_app import workflows


def test_empty_inventory_page_starts(engine: Engine, monkeypatch) -> None:
    monkeypatch.setenv("STOCKENTRY_DATABASE_URL", str(engine.url))
    workflows.clear()
    app = Path(__file__).resolve().parents[1] / "src/stockentry_guard/ui/streamlit_app.py"
    with patch("streamlit.download_button", wraps=st.download_button) as download_button:
        result = AppTest.from_file(str(app)).run(timeout=15)
    assert not result.exception
    assert [tab.label for tab in result.tabs] == ["Inventory", "Add Product", "Recent Activity"]
    assert result.metric[0].value == "0"
    assert any(button.label == "Download sample CSV" for button in result.download_button)
    sample_call = next(
        call
        for call in download_button.call_args_list
        if call.args and call.args[0] == "Download sample CSV"
    )
    assert sample_call.kwargs["file_name"] == "stockentry_sample_inventory.csv"
    rows = list(csv.reader(io.StringIO(sample_call.args[1].decode("utf-8"))))
    assert rows[0] == [
        "sku",
        "barcode",
        "name",
        "brand",
        "category",
        "variant",
        "size_value",
        "size_unit",
        "description",
    ]
    assert len(rows) == 4
    assert rows[1][:2] == ["001", "0001234567890"]
    assert rows[2][:2] == ["002", "0009876543210"]
    assert rows[3][1] == ""
    workflows.clear()


def test_review_check_and_create_without_ollama(engine: Engine, monkeypatch) -> None:
    monkeypatch.setenv("STOCKENTRY_DATABASE_URL", str(engine.url))
    workflows.clear()
    app = Path(__file__).resolve().parents[1] / "src/stockentry_guard/ui/streamlit_app.py"
    result = AppTest.from_file(str(app)).run(timeout=15)
    result.session_state["image_digest"] = None
    result.session_state["workflow_id"] = uuid4()
    result.session_state["extraction"] = ExtractionResult(
        ProductCandidate(name="Demo tea", brand="Demo"),
        ExtractionMetadata(0.8, (), "fake", "retail-product-v2", 1, 1),
    )
    for field in FIELDS:
        result.session_state[f"edit_{field}"] = {
            "name": "Demo tea",
            "brand": "Demo",
        }.get(field, "")
    result.run(timeout=15)
    assert not result.exception
    next(button for button in result.button if button.label == "Check duplicates").click().run(
        timeout=15
    )
    assert not result.exception
    assert any(message.value == "No matching product found" for message in result.success)
    assert any(
        message.value == "There are no existing products to compare yet." for message in result.info
    )
    assert not any("Top score:" in message.value for message in result.markdown)
    next(button for button in result.button if button.label == "Create new product").click().run(
        timeout=15
    )
    assert not result.exception
    assert result.metric[0].value == "1"
    assert any(message.value == "Product added successfully." for message in result.success)
    assert list(result.dataframe[0].value.columns) == [
        "Name",
        "Brand",
        "Variant",
        "Size",
        "Unit",
        "SKU",
        "Barcode",
        "Category",
    ]
    activity = result.dataframe[-1].value
    assert list(activity.columns) == ["Time", "Action", "Product", "Details"]
    assert "Product added" in activity["Action"].tolist()
    workflows.clear()
