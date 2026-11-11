"""Headless render of every UI page (skipped when the ui extra is not installed)."""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

PAGES = ["start", "network", "inputs", "gate", "advisory", "evaluate", "dictionary_page"]


def _app(page: str) -> AppTest:
    script = f"from neo.ui.pages import {page}\n{page}.render()\n"
    return AppTest.from_string(script, default_timeout=120)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_error(page):
    at = _app(page).run()
    assert not at.exception, [e.value for e in at.exception]


def test_what_if_run_produces_an_explained_advisory():
    at = _app("advisory").run()
    run = next(b for b in at.button if b.label == "Run NeO")
    at = run.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Result:" in m.value for m in at.markdown)
    assert at.tabs, "result tabs should render"


def test_cloud_launcher_starts_the_app():
    from tests.conftest import ROOT

    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("which wells should flow" in t.value for t in at.title)
