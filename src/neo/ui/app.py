"""NeO explainer UI. Run with ``uv run --extra ui neo ui`` (or ``streamlit run streamlit_app.py``)."""

from __future__ import annotations

import streamlit as st

from neo.ui.common import internal_banner
from neo.ui.nav import pages


def main() -> None:
    st.set_page_config(page_title="NeO explainer", page_icon="⛽", layout="wide")
    internal_banner()
    st.sidebar.caption("NeO v1 · gas-network optimisation advisor · synthetic reference network")
    st.navigation(list(pages())).run()


if __name__ == "__main__":
    main()
