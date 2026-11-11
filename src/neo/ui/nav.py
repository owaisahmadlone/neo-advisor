"""Page registry (kept apart from ``app`` so pages can link to each other without running the app)."""

from __future__ import annotations

from functools import cache

import streamlit as st


@cache
def pages() -> tuple:
    from neo.ui.pages import advisory, dictionary_page, evaluate, gate, inputs, network, start

    return (
        st.Page(start.render, title="Start here", icon="🧭", url_path="start", default=True),
        st.Page(network.render, title="1 · The network", icon="🗺️", url_path="network"),
        st.Page(inputs.render, title="2 · Inputs (landing zone)", icon="📥", url_path="inputs"),
        st.Page(gate.render, title="3 · Data gate & calibration", icon="🚦", url_path="gate"),
        st.Page(advisory.render, title="4 · Run an advisory", icon="🧮", url_path="advisory"),
        st.Page(evaluate.render, title="5 · Evaluate vs baselines", icon="📊", url_path="evaluate"),
        st.Page(dictionary_page.render, title="Dictionary & glossary", icon="📖", url_path="dictionary"),
    )


def page(url_path: str):
    return next(p for p in pages() if p.url_path == url_path)
