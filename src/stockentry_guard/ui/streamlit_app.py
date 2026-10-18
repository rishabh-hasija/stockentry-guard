"""Runnable scaffold only; onboarding workflows are not implemented yet."""

import streamlit as st


def main() -> None:
    st.set_page_config(page_title="StockEntry Guard")
    st.title("StockEntry Guard")
    st.write("AI-assisted inventory product onboarding for small retailers.")
    st.info("Project scaffold ready. Import, extraction, review, and export are coming next.")


if __name__ == "__main__":
    main()
